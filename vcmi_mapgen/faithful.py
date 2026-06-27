"""The faithful map representation shared by extraction, measurement, generation, and
the writer. A map is terrain (per-tile structured) + objects (authoritative ids +
animation + mask). Anything in this shape round-trips to an editor-valid .vmap.
"""

import json, re, glob, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vmapwrite

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


def to_vmap(fm, out_path, name=None):
    """faithful map dict -> editor-valid .vmap via the proven writer."""
    levels = [[[tile_string(c) for c in row] for row in lvl] for lvl in fm["terrain"]]
    objs = []
    n = 0
    for o in fm["objects"]:
        if not o.get("type"):
            continue
        n += 1
        tmpl = {"animation": o["animation"], "editorAnimation": "", "mask": o["mask"]}
        vf = visitable_from(o["mask"])
        if vf:
            tmpl["visitableFrom"] = vf
        objs.append(
            {
                "instanceName": f"{o['type']}_{n}",
                "l": o["l"],
                "type": o["type"],
                "subtype": o["subtype"],
                "template": tmpl,
                "x": o["x"],
                "y": o["y"],
            }
        )
    header, _, _, _ = vmapwrite.read_raw(
        glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap")[0]
    )
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
