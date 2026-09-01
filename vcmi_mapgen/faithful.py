"""The faithful map representation shared by extraction, measurement, generation, and
the writer. A map is terrain (per-tile structured) + objects (authoritative ids +
animation + mask). Anything in this shape round-trips to an editor-valid .vmap.
"""

import json, glob, os
import pathlib

from vcmi_mapgen.kit import vmap_format
from vcmi_mapgen.kit import paths as vcmi_paths


def to_vmap(fm, out_path, name=None):
    """faithful map dict -> editor-valid .vmap via the proven writer."""
    levels = [[[vmap_format.tile_string(c) for c in row] for row in lvl] for lvl in fm["terrain"]]
    objs = []
    n = 0
    for o in fm["objects"]:
        if not o.get("type"):
            continue
        n += 1
        tmpl = {"animation": o["animation"], "editorAnimation": "",
                "mask": vmap_format.export_mask(o)}
        vf = o.get("visitableFrom") or vmap_format.visitable_from(o["mask"])  # explicit override wins
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
        header, _, _, _ = vmap_format.read_raw(_rmg[0])
    else:
        _tpl = str(pathlib.Path(__file__).parent.parent / "data" / "vmap_header_template.json")
        header = json.load(open(_tpl))
    # Wire EACH player slot to its own starting town so the map is actually playable.
    # VCMI links a player to a town via mainTown = town_anchor - (2,2) (verified against
    # the random-map template); the town object itself stays owner=None. Earlier we
    # only gave player 0 a town, leaving every other player town-less => not playable.
    # Surface towns first, then put the start town (fm["main_town"]) on player 0.
    from vcmi_mapgen.kit import objects as _objects
    towns = [o for o in fm["objects"] if _objects.type_to_purpose(o.get("type")) == "TOWN"]
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
    vmap_format.write_vmap(out_path, header, levels, objs, name=name or fm.get("name", "generated"))
    return out_path


def load(path):
    return json.load(open(path))


def save(fm, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(fm, open(path, "w"))
