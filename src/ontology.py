"""Object ontology for VCMI/H3 maps.

Lifts raw (class_id, subclass) -> rich identity used by the map-generation model:
  - name:            human class name (from VCMI MapObjectID enum)
  - subtype:         resolved subtype name where canonical (resource/mine/faction), else raw id
  - purpose:         WHY the object exists (drives the "is its placement justified?" logic)
  - relational:      True if the object connects to another location/object (portals, gates...)
  - relational_key:  how the far endpoint is determined (for relational objects)
  - terrain_coupled: True if its placement is strongly tied to terrain type

Purpose tags and relational/terrain flags are HAND-AUTHORED game knowledge -- this is the
small irreducible ontology the data cannot teach (zero negative examples). Subtype *tables*
are canonical H3 orderings, verified against the corpus subclass distributions.
"""

import json, os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLASS_NAMES = {int(k): v for k, v in json.load(open(f"{_ROOT}/src/objclass_names.json")).items()}

# ---- canonical subtype tables (verified vs corpus subclass distributions) ----
RESOURCE = {
    0: "wood",
    1: "mercury",
    2: "ore",
    3: "sulfur",
    4: "crystal",
    5: "gems",
    6: "gold",
    7: "mithril",
}
# mine subclass -> resource it produces (same ordering as resources, 7 = abandoned)
MINE_RES = {
    0: "wood",
    1: "mercury",
    2: "ore",
    3: "sulfur",
    4: "crystal",
    5: "gems",
    6: "gold",
    7: "abandoned",
}
FACTION = {
    0: "castle",
    1: "rampart",
    2: "tower",
    3: "inferno",
    4: "necropolis",
    5: "dungeon",
    6: "stronghold",
    7: "fortress",
    8: "conflux",
}

# ---- purpose taxonomy: class name -> purpose ----
# Categories: TOWN DWELLING MINE RESOURCE_PILE REWARD_PICKUP GUARD TRANSPORT
# WATER_TRANSPORT STAT_PERMANENT BONUS_TEMP MANA SPELL_SKILL INFO BANK QUEST_GATE
# TERRAIN_MODIFIER HERO SPECIAL DECORATION
PURPOSE = {
    # towns / dwellings
    "TOWN": "TOWN",
    "RANDOM_TOWN": "TOWN",
    "CREATURE_GENERATOR1": "DWELLING",
    "CREATURE_GENERATOR2": "DWELLING",
    "CREATURE_GENERATOR3": "DWELLING",
    "CREATURE_GENERATOR4": "DWELLING",
    "RANDOM_DWELLING": "DWELLING",
    "RANDOM_DWELLING_LVL": "DWELLING",
    "RANDOM_DWELLING_FACTION": "DWELLING",
    "REFUGEE_CAMP": "DWELLING",
    "WAR_MACHINE_FACTORY": "DWELLING",
    # economy
    "MINE": "MINE",
    "ABANDONED_MINE": "MINE",
    "RESOURCE": "RESOURCE_PILE",
    "RANDOM_RESOURCE": "RESOURCE_PILE",
    "WINDMILL": "MINE",
    "WATER_WHEEL": "MINE",
    "MAGIC_SPRING": "MINE",
    "MYSTICAL_GARDEN": "MINE",
    "LEAN_TO": "REWARD_PICKUP",
    "TRADING_POST": "SPECIAL",
    "TRADING_POST_SNOW": "SPECIAL",
    "MARKET_OF_TIME": "SPECIAL",
    # reward pickups
    "TREASURE_CHEST": "REWARD_PICKUP",
    "CAMPFIRE": "REWARD_PICKUP",
    "FLOTSAM": "REWARD_PICKUP",
    "SEA_CHEST": "REWARD_PICKUP",
    "SHIPWRECK_SURVIVOR": "REWARD_PICKUP",
    "CORPSE": "REWARD_PICKUP",
    "SKULL": "REWARD_PICKUP",
    "WARRIORS_TOMB": "REWARD_PICKUP",
    "WAGON": "REWARD_PICKUP",
    "SCHOLAR": "REWARD_PICKUP",
    "ARTIFACT": "REWARD_PICKUP",
    "RANDOM_ART": "REWARD_PICKUP",
    "RANDOM_TREASURE_ART": "REWARD_PICKUP",
    "RANDOM_MINOR_ART": "REWARD_PICKUP",
    "RANDOM_MAJOR_ART": "REWARD_PICKUP",
    "RANDOM_RELIC_ART": "REWARD_PICKUP",
    "SPELL_SCROLL": "REWARD_PICKUP",
    "PANDORAS_BOX": "REWARD_PICKUP",
    "GRAIL": "SPECIAL",
    # guards
    "MONSTER": "GUARD",
    "RANDOM_MONSTER": "GUARD",
    "RANDOM_MONSTER_L1": "GUARD",
    "RANDOM_MONSTER_L2": "GUARD",
    "RANDOM_MONSTER_L3": "GUARD",
    "RANDOM_MONSTER_L4": "GUARD",
    "RANDOM_MONSTER_L5": "GUARD",
    "RANDOM_MONSTER_L6": "GUARD",
    "RANDOM_MONSTER_L7": "GUARD",
    # guarded combat banks
    "CREATURE_BANK": "BANK",
    "DERELICT_SHIP": "BANK",
    "CRYPT": "BANK",
    "SHIPWRECK": "BANK",
    "DRAGON_UTOPIA": "BANK",
    "PYRAMID": "BANK",
    # transport (relational)
    "MONOLITH_ONE_WAY_ENTRANCE": "TRANSPORT",
    "MONOLITH_ONE_WAY_EXIT": "TRANSPORT",
    "MONOLITH_TWO_WAY": "TRANSPORT",
    "SUBTERRANEAN_GATE": "TRANSPORT",
    "WHIRLPOOL": "TRANSPORT",
    "SHIPYARD": "WATER_TRANSPORT",
    "BOAT": "WATER_TRANSPORT",
    "LIGHTHOUSE": "WATER_TRANSPORT",
    # permanent stat boosts
    "LEARNING_STONE": "STAT_PERMANENT",
    "TREE_OF_KNOWLEDGE": "STAT_PERMANENT",
    "MARLETTO_TOWER": "STAT_PERMANENT",
    "STAR_AXIS": "STAT_PERMANENT",
    "GARDEN_OF_REVELATION": "STAT_PERMANENT",
    "MERCENARY_CAMP": "STAT_PERMANENT",
    "SCHOOL_OF_MAGIC": "STAT_PERMANENT",
    "SCHOOL_OF_WAR": "STAT_PERMANENT",
    "LIBRARY_OF_ENLIGHTENMENT": "STAT_PERMANENT",
    "ARENA": "STAT_PERMANENT",
    "HILL_FORT": "STAT_PERMANENT",
    "BORDERGUARD_CAMP": "STAT_PERMANENT",
    # temporary bonuses (luck/morale/movement)
    "IDOL_OF_FORTUNE": "BONUS_TEMP",
    "FOUNTAIN_OF_FORTUNE": "BONUS_TEMP",
    "FOUNTAIN_OF_YOUTH": "BONUS_TEMP",
    "RALLY_FLAG": "BONUS_TEMP",
    "OASIS": "BONUS_TEMP",
    "WATERING_HOLE": "BONUS_TEMP",
    "BUOY": "BONUS_TEMP",
    "MERMAID": "BONUS_TEMP",
    "SWAN_POND": "BONUS_TEMP",
    "FAERIE_RING": "BONUS_TEMP",
    "TEMPLE": "BONUS_TEMP",
    "STABLES": "BONUS_TEMP",
    "WELL_OF_YOUTH": "BONUS_TEMP",
    # mana
    "MAGIC_WELL": "MANA",
    # spell / skill
    "SHRINE_OF_MAGIC_INCANTATION": "SPELL_SKILL",
    "SHRINE_OF_MAGIC_GESTURE": "SPELL_SKILL",
    "SHRINE_OF_MAGIC_THOUGHT": "SPELL_SKILL",
    "WITCH_HUT": "SPELL_SKILL",
    "MAGIC_SPRING_SPELL": "SPELL_SKILL",
    # info
    "OBELISK": "INFO",
    "SIGN": "INFO",
    "OCEAN_BOTTLE": "INFO",
    "REDWOOD_OBSERVATORY": "INFO",
    "PILLAR_OF_FIRE": "INFO",
    "EYE_OF_MAGI": "INFO",
    "HUT_OF_MAGI": "INFO",
    "CARTOGRAPHER": "INFO",
    # quest / locked gates (relational)
    "SEER_HUT": "QUEST_GATE",
    "QUEST_GUARD": "QUEST_GATE",
    "BORDERGUARD": "QUEST_GATE",
    "BORDER_GATE": "QUEST_GATE",
    "KEYMASTER": "QUEST_GATE",
    # special terrain modifiers
    "MAGIC_PLAINS1": "TERRAIN_MODIFIER",
    "MAGIC_PLAINS2": "TERRAIN_MODIFIER",
    "CURSED_GROUND1": "TERRAIN_MODIFIER",
    "CURSED_GROUND2": "TERRAIN_MODIFIER",
    "CLOVER_FIELD": "TERRAIN_MODIFIER",
    "EVIL_FOG": "TERRAIN_MODIFIER",
    "HOLY_GROUNDS": "TERRAIN_MODIFIER",
    "LUCID_POOLS": "TERRAIN_MODIFIER",
    "FIERY_FIELDS": "TERRAIN_MODIFIER",
    "ROCKLANDS": "TERRAIN_MODIFIER",
    "MAGIC_CLOUDS": "TERRAIN_MODIFIER",
    # heroes / structural
    "HERO": "HERO",
    "RANDOM_HERO": "HERO",
    "PRISON": "HERO",
    "HERO_PLACEHOLDER": "HERO",
    "GARRISON": "SPECIAL",
    "GARRISON2": "SPECIAL",
    "EVENT": "SPECIAL",
    "FREELANCERS_GUILD": "SPECIAL",
    # trade / utility buildings
    "DEN_OF_THIEVES": "INFO",
    "COVER_OF_DARKNESS": "INFO",
    "BLACK_MARKET": "SPECIAL",
    "ALTAR_OF_SACRIFICE": "SPECIAL",
    "UNIVERSITY": "SPELL_SKILL",
    "SANCTUARY": "SPECIAL",
    "TAVERN": "SPECIAL",
    "DEN_OF_THIEVES2": "INFO",
    # water bonus / terrain
    "FAVORABLE_WINDS": "TERRAIN_MODIFIER",
    "SIRENS": "BONUS_TEMP",
    "WATERING_HOLE": "BONUS_TEMP",
}

# relational objects -> how the far endpoint is determined
RELATIONAL = {
    "MONOLITH_TWO_WAY": "same-subclass network (any other two-way monolith of equal subclass)",
    "MONOLITH_ONE_WAY_ENTRANCE": "matching one-way exit of equal subclass",
    "MONOLITH_ONE_WAY_EXIT": "matching one-way entrance of equal subclass",
    "SUBTERRANEAN_GATE": "nearest gate on the opposite level",
    "WHIRLPOOL": "another whirlpool (water network)",
    "BORDERGUARD": "keymaster tent of same subclass (colour)",
    "BORDER_GATE": "keymaster tent of same subclass (colour)",
    "KEYMASTER": "opens borderguards/gates of same subclass (colour)",
    "SEER_HUT": "quest target object (by id, stored in body)",
}

# terrain-coupled gameplay objects (placement strongly tied to terrain; most decoration also is)
TERRAIN_COUPLED = {
    "MINE",
    "ABANDONED_MINE",
    "TERRAIN_MODIFIER",
    "SHIPYARD",
    "LIGHTHOUSE",
    "WHIRLPOOL",
    "BOAT",
    "FLOTSAM",
    "SEA_CHEST",
    "SHIPWRECK",
    "SHIPWRECK_SURVIVOR",
    "BUOY",
    "MERMAID",
    "DERELICT_SHIP",
}


# class ids that are pure visual obstacles (incl. enum gaps 199/206-211 etc.)
def _is_decoration(name, cid):
    if name.startswith("CLASS_"):  # enum gap -> decorative obstacle
        return True
    return name in DECOR_NAMES


DECOR_NAMES = {
    "MOUNTAIN",
    "OAK_TREES",
    "PINE_TREES",
    "ROCK",
    "DEAD_VEGETATION",
    "SHRUB",
    "REEF",
    "TREES",
    "FLOWERS",
    "CRATER",
    "CACTUS",
    "LAVA_FLOW",
    "MUSHROOMS",
    "LAKE",
    "STUMP",
    "HOLE",
    "HEDGE",
    "KELP",
    "WILLOW_TREES",
    "YUCCA_TREES",
    "VOLCANO",
    "SAND_DUNE",
    "SAND_PIT",
    "CANYON",
    "MOSS",
    "BUSH",
    "PALM_TREE",
    "PLANT",
    "RIVER_DELTA",
    "FROZEN_LAKE",
    "OUTCROPPING",
    "MOUND",
    "LOG",
    "LAVA_LAKE",
    "SKULL",
    "CORPSE_DECO",
    "MANDRAKE",
    "FLOWERS2",
    "TAR_PIT",
    "GAZEBO_DECO",
}


def name_of(cid):
    return CLASS_NAMES.get(cid, f"CLASS_{cid}")


def resolve(cid, subclass):
    name = name_of(cid)
    # subtype resolution
    if name in ("RESOURCE", "RANDOM_RESOURCE"):
        subtype = RESOURCE.get(subclass, str(subclass)) if name == "RESOURCE" else "random"
    elif name in ("MINE", "ABANDONED_MINE"):
        subtype = MINE_RES.get(subclass, str(subclass))
    elif name == "TOWN":
        subtype = FACTION.get(subclass, str(subclass))
    elif name == "RANDOM_TOWN":
        subtype = "random"
    else:
        subtype = str(subclass)
    decor = _is_decoration(name, cid)
    purpose = "DECORATION" if decor else PURPOSE.get(name, "UNKNOWN")
    return {
        "name": name,
        "subtype": subtype,
        "purpose": purpose,
        "relational": name in RELATIONAL,
        "relational_key": RELATIONAL.get(name),
        "terrain_coupled": (
            purpose in ("MINE", "TERRAIN_MODIFIER", "WATER_TRANSPORT")
            or name in TERRAIN_COUPLED
            or decor
        ),
    }
