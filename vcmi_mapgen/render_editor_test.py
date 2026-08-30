"""Reliability tests for the H3-sprite rendering engine (render_editor.py).

These guard the parts that silently broke before: the DEF frame decoder across all
four H3 sprite formats (the format-3 block decoder in particular), terrain-tile
decoding, decode coverage over every sprite the engine actually composites, renderer
determinism, and the bit-exact "rebuilt == source" render (the identity guarantee at
the pixel level).

They require the local H3 sprite LOD files; the whole module is skipped when those
are absent (e.g. CI without a VCMI install).

Run: `uv run pytest vcmi_mapgen/render_editor_test.py -q`
"""
import os
import struct
import collections

import pytest

import vcmi_mapgen.render_editor as RE
import vcmi_mapgen.obj_resolve as OR
import vcmi_mapgen.faithful as FA
import vcmi_mapgen.zone_engine as ZE

TEST_MAP = "All for One"

# Skip the whole module if the H3 sprite LODs are not installed on this machine.
_lod_present = os.path.isdir(RE.LOD_DIR) and any(
    os.path.exists(os.path.join(RE.LOD_DIR, f)) for f in RE.LOD_FILES
)
pytestmark = pytest.mark.skipif(
    not _lod_present, reason=f"H3 sprite LOD files not found in {RE.LOD_DIR}"
)

# One representative DEF per H3 sprite compression format (discovered from the LOD):
#   0 = raw, 1 = per-line RLE, 2 = per-line typed RLE, 3 = per-32px-block typed RLE.
FORMAT_REPRESENTATIVES = {
    0: "grastl.def",   # grass terrain tile
    1: "adopb1b.def",  # animated decoration
    2: "dirtrd.def",   # dirt road overlay
    3: "AVLpntr7.def",  # 128x128 mountain (the format the decoder bug mangled)
}


# --------------------------------------------------------------------------- helpers
def _first_frame_header(defname):
    """(comp, fullW, fullH) read straight from the DEF's first frame header."""
    data = RE.lod().read(defname)
    assert data and len(data) >= 40, f"{defname}: empty/short DEF"
    pos = 16 + 256 * 3
    _bid, nframes = struct.unpack_from("<II", data, pos)
    pos += 16
    pos += nframes * 13                       # frame name table
    foff = struct.unpack_from("<" + "I" * nframes, data, pos)[0]
    comp, = struct.unpack_from("<I", data, foff + 4)
    fullw, fullh = struct.unpack_from("<II", data, foff + 8)
    return comp, fullw, fullh


def _nonempty(img):
    """True if any pixel is non-transparent."""
    return any(px[3] != 0 for px in img.getdata())


# --------------------------------------------------------------------------- tests
def test_lod_index_loaded():
    """The LOD index should expose the thousands of DEFs the renderer relies on."""
    n_defs = sum(1 for k in RE.lod()._files if k.endswith(".def"))
    assert n_defs > 1000, f"only {n_defs} DEFs indexed — LOD load looks broken"


@pytest.mark.parametrize("fmt,defname", sorted(FORMAT_REPRESENTATIVES.items()))
def test_all_four_def_formats_decode(fmt, defname):
    """Each of the four H3 sprite formats decodes to a frame of the header's
    declared full size, with real (non-transparent) content."""
    comp, fullw, fullh = _first_frame_header(defname)
    assert comp == fmt, f"{defname}: expected format {fmt}, header says {comp}"

    groups = RE.get_def(defname)
    assert groups and groups[0], f"{defname}: no frames decoded"
    frame0 = groups[0][0]
    assert frame0.size == (fullw, fullh), (
        f"{defname} (format {fmt}): decoded {frame0.size}, header full {fullw}x{fullh}"
    )
    assert _nonempty(frame0), f"{defname} (format {fmt}): decoded frame is fully transparent"


def test_every_terrain_tile_decodes():
    """Every terrain .def decodes, and terr_tile_img yields a 32x32 non-empty tile."""
    for tc, defname in RE.TERR_DEF.items():
        groups = RE.get_def(defname)
        assert groups and groups[0], f"terrain {tc} ({defname}) failed to decode"
        tile = RE.terr_tile_img(f"{tc}0_")
        assert tile.size == (RE.TILE, RE.TILE), f"{tc}: tile size {tile.size}"
        assert _nonempty(tile), f"{tc} ({defname}): terrain tile is fully transparent"


def test_known_object_sprites_decode():
    """A spot-check of recognizable object sprites (incl. a 128x128 mountain)."""
    known = {
        "AVLpntr7": (128, 128),   # mountain mass
        "AVLman30": (32, 32),     # small decoration
        "AVTrndm0": (64, 32),     # random treasure
    }
    for anim, expect in known.items():
        groups = RE.get_def(anim)
        assert groups and groups[0], f"{anim}: not decoded"
        frame0 = groups[0][0]
        assert frame0.size == expect, f"{anim}: size {frame0.size}, expected {expect}"
        assert _nonempty(frame0), f"{anim}: fully transparent"


def test_decode_coverage_over_corpus_sprites():
    """Reliability sweep: every distinct object sprite the engine would composite for
    the test map must, when present in the LOD, decode to a non-empty frame of the
    header-declared size. Sprites genuinely absent from the LOD are reported, not
    failed (that is a data-availability issue, not a decoder fault)."""
    fm = OR.load_faithful(TEST_MAP)
    anims = sorted({o["animation"] for o in fm["objects"] if o.get("animation")})
    assert anims, "no object animations found in the test map"

    absent, bad = [], []
    checked = 0
    for anim in anims:
        data = RE.lod().read(anim)
        if not data or len(data) < 40:          # not in LOD (data availability, not decoder)
            absent.append(anim)
            continue
        checked += 1
        try:
            _comp, fullw, fullh = _first_frame_header(anim)
            groups = RE.get_def(anim)
        except Exception as e:                  # noqa: BLE001 - record, don't crash the sweep
            bad.append((anim, f"exc:{e}"))
            continue
        if not (groups and groups[0]):
            bad.append((anim, "no frames"))
        elif groups[0][0].size != (fullw, fullh):
            bad.append((anim, f"{groups[0][0].size} != {fullw}x{fullh}"))
        elif not _nonempty(groups[0][0]):
            bad.append((anim, "transparent"))

    assert checked > 100, f"only {checked} sprites available to check"
    assert not bad, f"{len(bad)}/{checked} corpus sprites decoded wrong: {bad[:10]}"
    # Most of the map's sprites should be present; a few obscure DEFs may be absent.
    assert len(absent) <= 5, f"{len(absent)} sprites missing from LOD: {absent[:10]}"


def test_render_is_deterministic():
    """The same terrain + objects render to byte-identical pixels every time."""
    surf = [[f"gr{(x + y) % 4}_" for x in range(6)] for y in range(6)]
    objs = [
        {"x": 4, "y": 4, "l": 0, "type": "", "template": {"animation": "AVLpntr7", "mask": []}},
        {"x": 2, "y": 5, "l": 0, "type": "", "template": {"animation": "AVLman30", "mask": []}},
    ]
    a = RE.render_map(surf, ZE._paint_sort(objs))
    b = RE.render_map(surf, ZE._paint_sort(objs))
    assert a.size == b.size
    assert a.tobytes() == b.tobytes(), "renderer is not deterministic"


def test_rebuilt_map_renders_pixel_identical_to_source(tmp_path):
    """The bit-exact guarantee at the pixel level: an identity rebuild of the test map
    renders byte-for-byte identically to the source map through the same path."""
    import glob as _glob
    import vcmi_mapgen.vcmi_paths as _VP
    _randommaps = _glob.glob(os.path.join(_VP.vcmi_home(), "Maps", "RandomMaps", "*.vmap"))
    if not _randommaps:
        pytest.skip("VCMI template .vmap not available (no RandomMaps/*.vmap)")
    src_fm = OR.load_faithful(TEST_MAP)
    template = ZE.extract_template(TEST_MAP)
    rebuilt_fm, stats = ZE.rebuild_map(template, src_fm["terrain"], identity=True)
    assert stats["missing"] == 0, f"identity rebuild dropped zones: {stats}"

    src_vmap = str(tmp_path / "source.vmap")
    reb_vmap = str(tmp_path / "rebuilt.vmap")
    FA.to_vmap(src_fm, src_vmap, name="source")
    FA.to_vmap(rebuilt_fm, reb_vmap, name="rebuilt")

    ssurf, sobjs = RE.read_vmap(src_vmap)
    rsurf, robjs = RE.read_vmap(reb_vmap)
    src_img = RE.render_map(ssurf, ZE._paint_sort(sobjs))
    reb_img = RE.render_map(rsurf, ZE._paint_sort(robjs))

    assert src_img.size == reb_img.size
    assert src_img.tobytes() == reb_img.tobytes(), "rebuilt render differs from source"
