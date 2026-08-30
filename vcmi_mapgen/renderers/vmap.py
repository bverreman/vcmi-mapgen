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
    """Write player slots, team assignments, and victory condition into the .vmap."""
    import json
    import zipfile

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
                pl["allowedFactions"] = {"anyOf": [f"core:{t['subtype']}"]}
                pl.pop("randomFaction", None)
            else:
                pl.pop("allowedFactions", None)
                pl["randomFaction"] = True
            for vo in vobjs:
                if (vo["x"] == t["x"] and vo["y"] == t["y"]
                        and vo.get("l", 0) == t.get("l", 0)
                        and vo.get("type") in ("town", "randomTown")):
                    vo.setdefault("options", {})["owner"] = pid
                    break
        else:
            pl["canPlay"] = "AIOnly"
            pl.pop("mainTown", None)

    # strip special victory conditions; keep standardWin / standardDefeat
    h.pop("triggeredEvents", None)
    h["victoryIconIndex"] = 11
    h["victoryMessage"] = ""
    h["defeatIconIndex"] = 0
    h["defeatMessage"] = ""

    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in files.items():
            if name == "header.json":
                zout.writestr(name, json.dumps(h, indent=2))
            elif name == "objects.json":
                zout.writestr(name, json.dumps(vobjs, indent=2))
            else:
                zout.writestr(name, data)
    with open(vmap_path, "wb") as f:
        f.write(buf.getvalue())
