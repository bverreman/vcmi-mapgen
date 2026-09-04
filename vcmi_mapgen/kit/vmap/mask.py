"""Decode an h3m object template's raw block/visit bitmasks into the engine-internal
B/A/V/X footprint charset (folded in from the retired h3m2vmap.py)."""


def build_mask_from_h3m(block_mask, visit_mask):
    """6 rows x 8 cols. block bit 1=passable/0=blocked; visit bit 1=visitable. A cell carries
    TWO independent bits -> four states: 'B' blocked, 'V' passable, 'A' passable+visitable
    (stand on), 'X' blocked+visitable (a building's action tile -- visited from an adjacent
    tile). The mask is anchored bottom-right: bit b of row byte r is the tile at column b
    counted from the RIGHT edge (VCMI: usedTiles[5-i][7-j]). Reading bit (7-c) into column c
    mirrors every asymmetric footprint horizontally (the v5.2 sawmill-entrance bug) — bit c is
    the correct read for a left-to-right row. Kept in sync with ontology._decode_mask.
    """
    grid = [["V"] * 8 for _ in range(6)]
    for r in range(6):
        for c in range(8):
            blocked = not (block_mask[r] >> c) & 1
            visit = (visit_mask[r] >> c) & 1
            grid[r][c] = ("X" if blocked else "A") if visit else ("B" if blocked else "V")
    # In H3 EVERY visitable tile is also flagged blocked, so a lone pickup (resource/chest/monster)
    # looks identical to a building's gate. Distinguish by the solid BODY: only an object that has
    # pure-blocked ('B') body cells keeps its visit tile blocked ('X', visited from adjacent); a
    # bodyless single visit tile is a walk-onto pickup -> 'A' (passable). Restores passability.
    if not any(grid[r][c] == "B" for r in range(6) for c in range(8)):
        for r in range(6):
            for c in range(8):
                if grid[r][c] == "X":
                    grid[r][c] = "A"
    rows = [r for r in range(6) if any(ch != "V" for ch in grid[r])]
    cols = [c for c in range(8) if any(grid[r][c] != "V" for r in range(6))]
    if not rows:
        return ["B"]
    return [
        "".join(grid[r][c] for c in range(min(cols), max(cols) + 1))
        for r in range(min(rows), max(rows) + 1)
    ]
