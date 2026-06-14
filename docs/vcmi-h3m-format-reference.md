# VCMI H3M format reference

The [`vcmi-h3m-format-reference/`](../vcmi-h3m-format-reference/) folder at the repo root
holds verbatim C++ source files copied from the [VCMI engine](https://github.com/vcmi/vcmi),
kept as the authoritative reference for the Heroes 3 `.h3m` binary map format and
VCMI's object identifiers. They are **not compiled** — they document the byte layout that
[`vcmi_mapgen/h3m.py`](../vcmi_mapgen/h3m.py) parses and the IDs
[`vcmi_mapgen/vcmi_ids.py`](../vcmi_mapgen/vcmi_ids.py) resolves.

| File | What it documents |
|------|-------------------|
| `MapFormatH3M.cpp` / `.h` | The `.h3m` reader/writer — tile + object serialization order |
| `MapReaderH3M.cpp` / `.h` | Low-level field readers for the H3M stream |
| `MapFeaturesH3M.cpp` / `.h` | Per-format (RoE/AB/SoD) feature flags and sizes |
| `EntityIdentifiers.h` | Creature / artifact / faction / hero / spell index enums |

Update by re-copying from the matching VCMI release; do not edit by hand.
