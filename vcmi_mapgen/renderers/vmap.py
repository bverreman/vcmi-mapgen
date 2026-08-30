"""VmapRenderer — export a MapState as a playable VCMI .vmap file."""
from __future__ import annotations

import os

from vcmi_mapgen.pipeline import MapState
from vcmi_mapgen import faithful as FA
from vcmi_mapgen.vcmi_paths import project_root

ROOT = project_root()


class VmapRenderer:
    """Export a MapState to a VCMI editor .vmap, then apply player slots and teams.

    Usage::

        renderer = VmapRenderer(out_dir="out/vmap")
        path = renderer.render(state, "mymap.vmap", name="My Map", teams_spec="ffa")
    """

    def __init__(self, out_dir: str | None = None) -> None:
        self.out_dir = out_dir or str(ROOT / "out" / "vmap")

    def render(self, state: MapState, path: str, name: str = "pp-map",
               teams_spec: str = "ffa") -> str:
        """Write a .vmap file. Returns the resolved path."""
        if not os.path.isabs(path):
            path = os.path.join(self.out_dir, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # build level list in level order (faithful writer expects [cells0, cells1?])
        levels = [state.cells[lvl] for lvl in sorted(state.cells)]

        towns = [o for o in state.objs if o.get("purpose") == "TOWN"]
        fm = {
            "name": name,
            "terrain": levels,
            "objects": [o for o in state.objs if o.get("type")],
            "main_town": (
                {"l": towns[0].get("l", 0), "x": towns[0]["x"] - 2,
                 "y": towns[0]["y"] - 2}
                if towns else None
            ),
        }
        vp = FA.to_vmap(fm, path, name=name)

        if state.player_towns:
            teams = _parse_teams(teams_spec, len(state.player_towns))
            _apply_playability(vp, state.player_towns, teams)
        return vp


def _parse_teams(spec: str, n: int) -> list[int]:
    """Team list from a spec string: 'ffa', '2v2', '1v3', or '0,0,1,1'."""
    if not spec or spec == "ffa":
        return list(range(n))
    if "v" in spec:
        sizes = [int(s) for s in spec.split("v")]
        if sum(sizes) != n:
            raise ValueError(f"teams {spec!r} sums to {sum(sizes)}, but players={n}")
        return [ti for ti, s in enumerate(sizes) for _ in range(s)]
    out = [int(s) for s in spec.split(",")]
    if len(out) != n:
        raise ValueError(f"teams {spec!r} lists {len(out)} ids, but players={n}")
    return out


def _apply_playability(vmap_path: str, player_towns: list, teams: list[int]) -> None:
    """Deterministic playability overlay on an exported .vmap:

      1. exactly len(player_towns) playable slots, slot i wired to its designated town
         (any faction allowed — the towns are usually randomTown) — AND the town OBJECT
         itself gets `options.owner = <player>` (the header's mainTown alone does NOT
         assign ownership; without the owner the town stays neutral),
      2. the team matrix (`teams[i]` = team id of player i; VCMI allies equal ids),
      3. victory = DEFEAT ALL (the canonical standardWin triggered event; standardDefeat =
         7 days without town), any special victory conditions stripped.
    """
    import json
    import zipfile
    from collections import defaultdict

    with zipfile.ZipFile(vmap_path) as z:
        files = {n: z.read(n) for n in z.namelist()}

    h = json.loads(files["header.json"].decode())
    vobjs = json.loads(files["objects.json"].decode())
    pids = sorted(p for p, pl in h["players"].items() if isinstance(pl, dict))

    for i, pid in enumerate(pids):
        pl = h["players"][pid]
        if i < len(player_towns):
            t = player_towns[i]
            pl["mainTown"] = {"generateHero": True, "l": t.get("l", 0),
                              "x": t["x"] - 2, "y": t["y"] - 2}
            pl["canPlay"] = "PlayerOrAI"
            pl["team"] = int(teams[i])
            if t.get("type") == "town":
                # concrete start town (spare-neutral top-up): the lobby must not offer
                # factions the map cannot honour — restrict to the authored one, exactly
                # like VCMI's own RMG maps do
                pl["allowedFactions"] = {"anyOf": [f"core:{t['subtype']}"]}
                pl.pop("randomFaction", None)
            else:
                # randomTown start: any faction; VCMI resolves the OWNED random town to
                # the lobby pick (CGTownInstance::randomizeFaction). PlayerInfo::defaultCastle()
                # only returns RANDOM when isFactionRandom is set — an absent/permissive
                # allowedFactions alone still defaults the lobby dropdown to the first
                # faction (Castle) sorted by id. Field name from MapFormatJson.cpp's
                # serializePlayerInfo: handler.serializeBool("randomFaction", ...).
                pl.pop("allowedFactions", None)
                pl["randomFaction"] = True
            for vo in vobjs:                         # ownership lives on the town object
                if (vo["x"] == t["x"] and vo["y"] == t["y"]
                        and vo.get("l", 0) == t.get("l", 0)
                        and vo.get("type") in ("town", "randomTown")):
                    vo.setdefault("options", {})["owner"] = pid
                    break
        else:
            pl["mainTown"] = None
            pl["canPlay"] = "false"
            pl.pop("team", None)
    # VCMI's lobby/map-select screen reads alliances from this top-level grouping —
    # not from each player's individual "team" int above — so it must be set for
    # the UI to show teams at all. Real VCMI RMG maps omit the key entirely for FFA.
    groups = defaultdict(list)
    for i, pid in enumerate(pids[:len(player_towns)]):
        groups[int(teams[i])].append(pid)
    allied = [members for members in groups.values() if len(members) > 1]
    if allied:
        h["teams"] = allied
    else:
        h.pop("teams", None)
    files["objects.json"] = json.dumps(vobjs, indent=1).encode()
    MSG = {"exactStrings": None, "localStrings": None, "message": [2], "numbers": None}
    h["triggeredEvents"] = {
        "standardVictory": {
            "condition": ["standardWin", {"type": "", "value": -1}],
            "effect": {"messageToSend": {"exactStrings": None, "localStrings": None,
                                         "message": None, "numbers": None,
                                         "stringsTextID": None}, "type": "victory"},
            "message": dict(MSG, stringsTextID=["core.genrltxt.659"])},
        "standardDefeat": {
            "condition": ["daysWithoutTown", {"type": "", "value": 7}],
            "effect": {"messageToSend": {"exactStrings": None, "localStrings": None,
                                         "message": None, "numbers": None,
                                         "stringsTextID": None}, "type": "defeat"},
            "message": dict(MSG, stringsTextID=["core.genrltxt.7"])}}
    h["victoryIconIndex"] = 11                       # "defeat all enemies"
    h["victoryMessage"] = dict(MSG, stringsTextID=["core.vcdesc.0"])
    h["defeatIconIndex"] = 3
    h["defeatMessage"] = dict(MSG, stringsTextID=["core.lcdesc.0"])
    files["header.json"] = json.dumps(h, indent=1).encode()

    with zipfile.ZipFile(vmap_path, "w", zipfile.ZIP_DEFLATED) as zo:
        for name, data in files.items():
            zo.writestr(name, data)
