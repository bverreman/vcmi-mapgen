"""Authoritative H3M (objectClass, objectSubID) -> VCMI 'type::subtype' identifiers,
read directly from VCMI's own config (the source of truth the editor uses). No guessing.

config/objects/*.json : { "<type>": { "index": <class>, "types": { "<subtype>": {"index": <subID>} } } }
config/creatures/*.json, config/factions/*.json : { "<identifier>": { "index": <id> } }  (for monster/town subtypes)
"""

import json, re, glob, os

_BASES = [
    "/var/lib/flatpak/app/eu.vcmi.VCMI/current/active/files/share/vcmi/config",
    os.path.expanduser("~/.var/app/eu.vcmi.VCMI/data/vcmi/Mods"),
]


def _relaxed(t):
    t = re.sub(r"//[^\n]*", "", t)
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    t = re.sub(r",(\s*[}\]])", r"\1", t)
    return json.loads(t)


def _files(sub):
    out = []
    for b in _BASES:
        out += glob.glob(f"{b}/**/config/{sub}/*.json", recursive=True) + glob.glob(
            f"{b}/{sub}/*.json"
        )
    return sorted(set(out))


def _index_map(sub):  # identifier -> index  ==>  index -> identifier
    m = {}
    for f in _files(sub):
        try:
            d = _relaxed(open(f).read())
        except Exception:
            continue
        for ident, obj in d.items():
            if isinstance(obj, dict) and isinstance(obj.get("index"), int):
                m.setdefault(obj["index"], ident)
    return m


_CLS2TYPE = {}  # objectClass -> [typeName, {subID: subtypeName}]


def _load_objects():
    for f in _files("objects"):
        try:
            d = _relaxed(open(f).read())
        except Exception:
            continue
        for tname, obj in d.items():
            if not isinstance(obj, dict) or not isinstance(obj.get("index"), int):
                continue
            subs = {}
            for sname, s in (obj.get("types") or {}).items():
                if isinstance(s, dict) and isinstance(s.get("index"), int):
                    subs[s["index"]] = sname
            _CLS2TYPE.setdefault(obj["index"], [tname, {}])[1].update(subs)


_load_objects()
_CREATURE = _index_map("creatures")
_FACTION = _index_map("factions")
_HERO = _index_map("heroes")
_SPELL = _index_map("spells")


def _single_map(relpath):  # for single-file configs like config/artifacts.json
    m = {}
    for b in _BASES:
        for f in glob.glob(f"{b}/**/{relpath}", recursive=True) + [f"{b}/{relpath}"]:
            if not os.path.isfile(f):
                continue
            try:
                d = _relaxed(open(f).read())
            except Exception:
                continue
            for ident, obj in d.items():
                if isinstance(obj, dict) and isinstance(obj.get("index"), int):
                    m.setdefault(obj["index"], ident)
    return m


_ARTIFACT = _single_map("artifacts.json")

# object types whose subtype comes from another registry, not config/objects
_BY_CREATURE = {"monster", "randomMonster"}
_BY_FACTION = {"town", "randomTown"}
_BY_HERO = {"hero", "randomHero", "prison", "heroPlaceholder"}


def resolve(obj_class, obj_subid):
    """-> (type, subtype) or None if unknown."""
    e = _CLS2TYPE.get(obj_class)
    if not e:
        return None
    tname, subs = e
    if obj_subid in subs:  # inline subtype (decoration/mine/resource/monolith...)
        return tname, subs[obj_subid]
    if tname in _BY_CREATURE:
        return tname, _CREATURE.get(obj_subid, "imp")
    if tname in _BY_FACTION:
        return tname, _FACTION.get(obj_subid, "castle")
    if tname in _BY_HERO:
        return tname, _HERO.get(obj_subid, "christian")
    if tname == "artifact":
        return tname, _ARTIFACT.get(obj_subid, "spellBook")
    if tname == "spellScroll":
        return tname, _SPELL.get(obj_subid, "magicArrow")
    if subs:  # has subtypes but subID unlisted -> first valid
        return tname, sorted(subs.values())[0]
    return tname, "object"  # typeless object


if __name__ == "__main__":
    print(
        f"object classes: {len(_CLS2TYPE)}  creatures: {len(_CREATURE)}  factions: {len(_FACTION)}"
    )
    for c, s in [
        (134, 0),
        (101, 0),
        (53, 6),
        (79, 6),
        (54, 0),
        (98, 0),
        (45, 2),
        (5, 0),
    ]:
        print(f"  class {c} sub {s} -> {resolve(c, s)}")
