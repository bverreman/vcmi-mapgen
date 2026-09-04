"""The VmapDocument model: a full, round-trip-safe in-memory representation of a
VCMI .vmap file. Fields the pipeline actually needs to read or write (terrain,
objects, player slots, teams, victory/defeat) are modeled structurally; every
other header/player key survives untouched in `extra` so a real (possibly
hand-authored) .vmap round-trips losslessly even though this model doesn't
enumerate 100% of VCMI's header schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VmapObject:
    """One `objects.json` entry. `mask` is the VCMI-charset (' 0VBHAT') footprint as
    stored in the file -- see `kit.vmap.terrain.vcmi_mask` for why this is NOT the same
    charset the engine's internal `mask_cells`/`is_blocking` expect."""

    instance_name: str
    type: str
    subtype: str
    l: int
    x: int
    y: int
    animation: str
    editor_animation: str = ""
    mask: list = field(default_factory=list)
    visitable_from: list | None = None
    options: dict | None = None


@dataclass
class PlayerSlot:
    """One `header.json` `players.<color>` entry. `id` is the VCMI color key
    (blue/green/orange/pink/purple/red/tan/teal), not a "playerN" index."""

    id: str
    can_play: str = "false"
    team: int | None = None
    main_town: dict | None = None
    allowed_factions: dict | None = None
    random_faction: bool | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class VmapDocument:
    """A full `.vmap`. `terrain` is `[level][row] -> [tile_string, ...]` (VCMI tile
    tokens, e.g. "gr0_") -- the only lossless representation, since a hand-authored
    map's terrain is string-only in the file."""

    name: str
    width: int
    height: int
    two_level: bool
    terrain: list = field(default_factory=list)
    objects: list = field(default_factory=list)          # list[VmapObject]
    players: list = field(default_factory=list)           # list[PlayerSlot]
    teams: list | None = None                             # list[list[str]] (color groups)
    victory_icon_index: int | None = None
    victory_message: dict | None = None
    defeat_icon_index: int | None = None
    defeat_message: dict | None = None
    triggered_events: dict | None = None
    extra: dict = field(default_factory=dict)             # raw header minus modeled keys

    def player(self, color: str) -> PlayerSlot | None:
        return next((p for p in self.players if p.id == color), None)
