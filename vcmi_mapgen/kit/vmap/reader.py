"""VmapDocument reader: unzips a `.vmap` and parses it into a full, structured,
round-trip-safe `VmapDocument` -- header players/teams/victory/defeat, every
object's identity/mask/options, and terrain as VCMI tile strings.

This is a STRUCTURAL reader only: it does not reconstruct the engine-internal mask
charset (see `kit.vmap.terrain.vcmi_mask`'s docstring) -- `VmapObject.mask` is exactly
what the file's `template.mask` says, which is lossy for the 'X' vs 'A' distinction.
Callers that need the internal charset (blocking/visitable classification) must
re-derive it from the ontology by object identity, not from this field.
"""
from __future__ import annotations

import json
import re
import zipfile

from vcmi_mapgen.kit.vmap.document import PlayerSlot, VmapDocument, VmapObject

_PLAYER_MODELED = {"canPlay", "team", "mainTown", "allowedFactions", "randomFaction"}
_HEADER_MODELED = {
    "name", "mapLevels", "players", "teams", "triggeredEvents",
    "victoryIconIndex", "victoryMessage", "defeatIconIndex", "defeatMessage",
}


def _relaxed(text):
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


def _player_slot(color, pl):
    return PlayerSlot(
        id=color,
        can_play=pl.get("canPlay", "false"),
        team=pl.get("team"),
        main_town=pl.get("mainTown"),
        allowed_factions=pl.get("allowedFactions"),
        random_faction=pl.get("randomFaction"),
        extra={k: v for k, v in pl.items() if k not in _PLAYER_MODELED},
    )


def _object(o):
    tmpl = o.get("template", {})
    return VmapObject(
        instance_name=o.get("instanceName", ""),
        type=o.get("type", ""),
        subtype=o.get("subtype", ""),
        l=o.get("l", 0),
        x=o["x"], y=o["y"],
        animation=tmpl.get("animation", ""),
        editor_animation=tmpl.get("editorAnimation", ""),
        mask=tmpl.get("mask", []),
        visitable_from=tmpl.get("visitableFrom"),
        options=o.get("options"),
    )


def read(path: str) -> VmapDocument:
    z = zipfile.ZipFile(path)
    names = z.namelist()
    header = _relaxed(z.read("header.json").decode("utf-8", "replace"))
    surf = _relaxed(z.read("surface_terrain.json").decode())
    under = (
        _relaxed(z.read("underground_terrain.json").decode())
        if "underground_terrain.json" in names
        else None
    )
    raw_objs = _relaxed(z.read("objects.json").decode("utf-8", "replace"))

    name_struct = header.get("name") or {}
    name = (name_struct.get("exactStrings") or [""])[0] or ""
    levels = header.get("mapLevels", {})
    surface = levels.get("surface", {})
    width = surface.get("width", len(surf[0]) if surf else 0)
    height = surface.get("height", len(surf) if surf else 0)
    two_level = "underground" in levels and under is not None

    terrain = [surf] + ([under] if two_level else [])
    players = [
        _player_slot(color, pl)
        for color, pl in header.get("players", {}).items()
        if isinstance(pl, dict)
    ]

    return VmapDocument(
        name=name,
        width=width,
        height=height,
        two_level=two_level,
        terrain=terrain,
        objects=[_object(o) for o in raw_objs],
        players=players,
        teams=header.get("teams"),
        victory_icon_index=header.get("victoryIconIndex"),
        victory_message=header.get("victoryMessage"),
        defeat_icon_index=header.get("defeatIconIndex"),
        defeat_message=header.get("defeatMessage"),
        triggered_events=header.get("triggeredEvents"),
        extra={k: v for k, v in header.items() if k not in _HEADER_MODELED},
    )
