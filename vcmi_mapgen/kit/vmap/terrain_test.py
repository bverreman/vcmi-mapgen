from vcmi_mapgen.kit.vmap.terrain import decode_tile_string, tile_string, vcmi_mask, visitable_from


def test_tile_string_round_trips_through_decode():
    cells = [
        {"t": 2, "view": 0, "m": 0, "rt": 0, "rd": 0, "ot": 0, "od": 0},   # bare grass
        {"t": 8, "view": 3, "m": 3, "rt": 0, "rd": 0, "ot": 0, "od": 0},   # water, full mirror
        {"t": 2, "view": 5, "m": 1, "rt": 1, "rd": 2, "ot": 0, "od": 0},   # river only
        {"t": 2, "view": 5, "m": 2, "rt": 0, "rd": 0, "ot": 2, "od": 4},   # road only
        {"t": 6, "view": 12, "m": 0, "rt": 3, "rd": 1, "ot": 1, "od": 7},  # river + road
    ]
    for c in cells:
        s = tile_string(c)
        assert decode_tile_string(s) == c, f"round-trip broke for {c} -> {s!r}"


def test_decode_tile_string_rejects_garbage():
    import pytest
    with pytest.raises(ValueError):
        decode_tile_string("not-a-tile")


def test_vcmi_mask_collapses_blocked_entrance_to_visitable():
    """This loses information on purpose -- see the docstring. Callers needing the
    internal charset must re-derive it from the ontology, never from this output."""
    assert vcmi_mask(["BBB", "BXB", "BBB"]) == ["BBB", "BAB", "BBB"]


def test_visitable_from_distinguishes_building_from_freestanding():
    assert visitable_from(["BBB", "BXB"]) == ["---", "+-+", "+++"]     # blocked body -> building
    assert visitable_from(["A"]) == ["+++", "+-+", "+++"]              # no blocked body
    assert visitable_from(["VVV", "VVV"]) is None                      # pure decoration
