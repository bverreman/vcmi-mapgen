"""The faithful map representation shared by extraction, measurement, generation, and
the writer. A map is terrain (per-tile structured) + objects (authoritative ids +
animation + mask). Anything in this shape round-trips to an editor-valid .vmap.
"""

import json, re, glob, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vmapwrite
import ontology
import vcmi_paths

TCODE = {
    0: "dt",
    1: "sa",
    2: "gr",
    3: "sn",
    4: "sw",
    5: "rg",
    6: "sb",
    7: "lv",
    8: "wt",
    9: "rc",
}
RIVER = {1: "clrv", 2: "icyrv", 3: "mudrv", 4: "lavrv"}
ROAD = {1: "dirtrd", 2: "gravrd", 3: "cobbrd"}


def _mir(m):
    h, v = m & 1, m & 2
    return "+" if (h and v) else "|" if v else "-" if h else "_"


def tile_string(c):
    s = f"{TCODE.get(c['t'], 'gr')}{c['view']}{_mir(c.get('m', 0))}"
    if c.get("rt"):
        s += f"{RIVER.get(c['rt'], 'clrv')}{c.get('rd', 0)}_"
    if c.get("ot"):
        s += f"{ROAD.get(c['ot'], 'dirtrd')}{c.get('od', 0)}_"
    return s


def visitable_from(mask):
    """The 3x3 approach grid VCMI needs for visitable templates. Buildings (have
    blocked body) are entered from the sides/below; free-standing pickups/monsters
    from all 8 directions. None for pure decoration (no visitable tile)."""
    if not any(ch in "AX" for r in mask for ch in r):     # 'A' or 'X' = a visitable tile
        return None
    if any(ch in "BX" for r in mask for ch in r):         # has a blocked body -> a building
        return ["---", "+-+", "+++"]
    return ["+++", "+-+", "+++"]


def vcmi_mask(mask):
    """Translate an engine-internal mask to VCMI's template charset for .vmap export.

    Our masks use 'X' for a blocked ENTRANCE cell (entered from below). VCMI's
    ObjectTemplate parser only knows ' 0VBHAT' and logs "Unrecognized char X in template
    mask", dropping the cell to FREE — which made every mine/town silently unvisitable
    in-game (no VISITABLE cell survived). VCMI's 'A' = VISIBLE|BLOCKED|VISITABLE is the
    exact semantic of our 'X' (and of our walk-on 'A' — monsters/pickups are
    blocked-visitable in H3), so both map onto it."""
    return [row.replace("X", "A") for row in mask]


def _trim_v(mask):
    """The mask minus its all-'V' border rows/columns — the footprint core two masks must
    share to be the same object shape."""
    rows = [i for i, r in enumerate(mask) if set(r) - {"V"}]
    cols = [c for c in range(len(mask[0])) if any(row[c] != "V" for row in mask)]
    if not rows or not cols:
        return ["V"]
    return [mask[i][min(cols):max(cols) + 1] for i in range(min(rows), max(rows) + 1)]


def export_mask(o):
    """The template mask written to the .vmap: the ontology's sprite-extent mask
    (`vmap_mask_of` — VCMI draws an object only on mask-covered tiles, so the bbox-trimmed
    internal mask truncates tall sprites in-game) when its footprint core agrees with the
    instance mask; otherwise the instance mask translated to VCMI's charset (the editor
    table and a map instance legitimately disagree for a handful of corpus dwellings)."""
    inst = vcmi_mask(o["mask"])
    vm = ontology.vmap_mask_of(o["animation"])
    return vm if vm and _trim_v(vm) == _trim_v(inst) else inst


def to_vmap(fm, out_path, name=None):
    """faithful map dict -> editor-valid .vmap via the proven writer."""
    levels = [[[tile_string(c) for c in row] for row in lvl] for lvl in fm["terrain"]]
    objs = []
    n = 0
    for o in fm["objects"]:
        if not o.get("type"):
            continue
        n += 1
        tmpl = {"animation": o["animation"], "editorAnimation": "",
                "mask": export_mask(o)}
        vf = visitable_from(o["mask"])                # semantics from the INTERNAL mask
        if vf:
            tmpl["visitableFrom"] = vf
        vo = {
            "instanceName": f"{o['type']}_{n}",
            "l": o["l"],
            "type": o["type"],
            "subtype": o["subtype"],
            "template": tmpl,
            "x": o["x"],
            "y": o["y"],
        }
        if o.get("options"):                          # e.g. monster character, town fort
            vo["options"] = dict(o["options"])
        objs.append(vo)
    # Resolve dwelling->town faction links: the generator marks `sameAsTown` with the
    # town's [x, y, l] (instance names are minted only here); VCMI wants the town's
    # instanceName. A marker whose town vanished is dropped (dwelling stays any-faction).
    town_names = {(vo["x"], vo["y"], vo["l"]): vo["instanceName"]
                  for vo in objs if vo["type"] in ("town", "randomTown")}
    for vo in objs:
        tag = (vo.get("options") or {}).get("sameAsTown")
        if isinstance(tag, list):
            town_name = town_names.get(tuple(tag))
            if town_name:
                vo["options"]["sameAsTown"] = town_name
            else:
                del vo["options"]["sameAsTown"]
                if not vo["options"]:
                    del vo["options"]
    _rmg = glob.glob(os.path.join(vcmi_paths.vcmi_home(), "Maps", "RandomMaps", "*.vmap"))
    if _rmg:
        header, _, _, _ = vmapwrite.read_raw(_rmg[0])
    else:
        _tpl = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "vmap_header_template.json")
        header = json.load(open(_tpl))
    # Wire EACH player slot to its own starting town so the map is actually playable.
    # VCMI links a player to a town via mainTown = town_anchor - (2,2) (verified against
    # the random-map template); the town object itself stays owner=None. Earlier we
    # only gave player 0 a town, leaving every other player town-less => not playable.
    # Surface towns first, then put the start town (fm["main_town"]) on player 0.
    import traverse as TR
    towns = [o for o in fm["objects"] if TR.TYPE2PURPOSE.get(o.get("type")) == "TOWN"]
    towns.sort(key=lambda o: (o.get("l", 0), o["y"], o["x"]))
    mt = fm.get("main_town")
    if mt is not None:  # start town first => player 0
        towns.sort(key=lambda o: not (o.get("l", 0) == mt["l"]
                                      and o["x"] - 2 == mt["x"] and o["y"] - 2 == mt["y"]))
    pids = sorted(p for p, pl in header.get("players", {}).items() if isinstance(pl, dict))
    for i, pid in enumerate(pids):
        pl = header["players"][pid]
        if i < len(towns):
            t = towns[i]
            pl["mainTown"] = {"generateHero": True, "l": t.get("l", 0),
                              "x": t["x"] - 2, "y": t["y"] - 2}
            pl["canPlay"] = "PlayerOrAI"
        else:                       # no town for this slot -> not a participant
            pl["mainTown"] = None
            pl["canPlay"] = "false"
    vmapwrite.write_vmap(out_path, header, levels, objs, name=name or fm.get("name", "generated"))
    return out_path


def load(path):
    return json.load(open(path))


def save(fm, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(fm, open(path, "w"))
