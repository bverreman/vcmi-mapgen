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

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
CLASS_NAMES = {
    int(k): v for k, v in json.load(open(os.path.join(_HERE, "objclass_names.json"))).items()
}

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
#
# The 19 fine-grained purposes above are the model's working taxonomy. On top of them
# sits a coarse 4-way *macro-cluster* (see CLUSTERS / cluster_of below) used by the image
# generator: DECORATION (visual), QUEST_PAIR (needs a partner elsewhere), GATE (zone/terrain
# separator), VISIBLE (everything else). The block comments mark each purpose's cluster.
PURPOSE = {
    # [VISIBLE] towns / dwellings
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
    # [VISIBLE] economy
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
    # [VISIBLE] reward pickups
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
    # [VISIBLE] guards
    "MONSTER": "GUARD",
    "RANDOM_MONSTER": "GUARD",
    "RANDOM_MONSTER_L1": "GUARD",
    "RANDOM_MONSTER_L2": "GUARD",
    "RANDOM_MONSTER_L3": "GUARD",
    "RANDOM_MONSTER_L4": "GUARD",
    "RANDOM_MONSTER_L5": "GUARD",
    "RANDOM_MONSTER_L6": "GUARD",
    "RANDOM_MONSTER_L7": "GUARD",
    # [VISIBLE] guarded combat banks
    "CREATURE_BANK": "BANK",
    "DERELICT_SHIP": "BANK",
    "CRYPT": "BANK",
    "SHIPWRECK": "BANK",
    "DRAGON_UTOPIA": "BANK",
    "PYRAMID": "BANK",
    # [QUEST_PAIR] transport portals (relational: paired endpoints)
    "MONOLITH_ONE_WAY_ENTRANCE": "TRANSPORT",
    "MONOLITH_ONE_WAY_EXIT": "TRANSPORT",
    "MONOLITH_TWO_WAY": "TRANSPORT",
    "SUBTERRANEAN_GATE": "TRANSPORT",
    "WHIRLPOOL": "TRANSPORT",
    "SHIPYARD": "WATER_TRANSPORT",
    "BOAT": "WATER_TRANSPORT",
    "LIGHTHOUSE": "WATER_TRANSPORT",
    # [VISIBLE] permanent stat boosts
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
    # [VISIBLE] temporary bonuses (luck/morale/movement)
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
    # [VISIBLE] mana
    "MAGIC_WELL": "MANA",
    # [VISIBLE] spell / skill
    "SHRINE_OF_MAGIC_INCANTATION": "SPELL_SKILL",
    "SHRINE_OF_MAGIC_GESTURE": "SPELL_SKILL",
    "SHRINE_OF_MAGIC_THOUGHT": "SPELL_SKILL",
    "WITCH_HUT": "SPELL_SKILL",
    "MAGIC_SPRING_SPELL": "SPELL_SKILL",
    # [VISIBLE] info
    "OBELISK": "INFO",
    "SIGN": "INFO",
    "OCEAN_BOTTLE": "INFO",
    "REDWOOD_OBSERVATORY": "INFO",
    "PILLAR_OF_FIRE": "INFO",
    "EYE_OF_MAGI": "INFO",
    "HUT_OF_MAGI": "INFO",
    "CARTOGRAPHER": "INFO",
    # [GATE / QUEST_PAIR] quest gates -- splits by object type:
    #   borderGate/borderGuard/questGuard -> GATE; seerHut/keymasterTent -> QUEST_PAIR
    "SEER_HUT": "QUEST_GATE",
    "QUEST_GUARD": "QUEST_GATE",
    "BORDERGUARD": "QUEST_GATE",
    "BORDER_GATE": "QUEST_GATE",
    "KEYMASTER": "QUEST_GATE",
    # [VISIBLE] special terrain modifiers
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
    # [VISIBLE] heroes / structural
    "HERO": "HERO",
    "RANDOM_HERO": "HERO",
    "PRISON": "HERO",
    "HERO_PLACEHOLDER": "HERO",
    "GARRISON": "SPECIAL",
    "GARRISON2": "SPECIAL",
    "EVENT": "SPECIAL",
    "FREELANCERS_GUILD": "SPECIAL",
    # [VISIBLE] trade / utility buildings
    "DEN_OF_THIEVES": "INFO",
    "COVER_OF_DARKNESS": "INFO",
    "BLACK_MARKET": "SPECIAL",
    "ALTAR_OF_SACRIFICE": "SPECIAL",
    "UNIVERSITY": "SPELL_SKILL",
    "SANCTUARY": "SPECIAL",
    "TAVERN": "SPECIAL",
    "DEN_OF_THIEVES2": "INFO",
    # [VISIBLE] water bonus / terrain
    "FAVORABLE_WINDS": "TERRAIN_MODIFIER",
    "SIRENS": "BONUS_TEMP",
    "WATERING_HOLE": "BONUS_TEMP",
}

# ---- macro-clusters: coarse 4-way grouping used by the image generator ----
# A purpose maps wholesale to a cluster, EXCEPT QUEST_GATE which splits per object:
#   borderGate / borderGuard / questGuard -> GATE  (a physical separator between zones/terrains)
#   seerHut / keymasterTent               -> QUEST_PAIR  (the half that implies a partner elsewhere)
CLUSTERS = ("DECORATION", "VISIBLE", "GATE", "QUEST_PAIR")
GATE_TYPES = {"borderGate", "borderGuard", "questGuard"}  # objlib `type` strings
GATE_NAMES = {"BORDER_GATE", "BORDERGUARD", "QUEST_GUARD"}  # ontology enum names


def cluster_of(purpose, name=None, type_=None):
    """Macro-cluster for an object, from its purpose plus (when QUEST_GATE) its enum name
    or objlib `type`. Usable from both the enum-name path (resolve) and the objlib-type
    path (the catalog renderer)."""
    if purpose == "DECORATION":
        return "DECORATION"
    if purpose == "TRANSPORT":
        return "QUEST_PAIR"
    if purpose == "QUEST_GATE":
        if name in GATE_NAMES or type_ in GATE_TYPES:
            return "GATE"
        return "QUEST_PAIR"
    return "VISIBLE"


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
        "cluster": cluster_of(purpose, name=name),
        "relational": name in RELATIONAL,
        "relational_key": RELATIONAL.get(name),
        "terrain_coupled": (
            purpose in ("MINE", "TERRAIN_MODIFIER", "WATER_TRANSPORT")
            or name in TERRAIN_COUPLED
            or decor
        ),
    }


# ---------------------------------------------------------------------------
# Full taxonomy tree: CLUSTER -> PURPOSE -> type -> terrain -> leaf
# ---------------------------------------------------------------------------
# The hand-authored layers above (cluster, purpose) are the irreducible ontology; the lower layers
# (type -> terrain -> concrete sprite) complete the tree, every parent->child edge down to the leaf
# sprite. The full tree is HARDCODED below as TAXONOMY -- it is the single source of truth for the
# catalog renderer (zone_engine render-ontology). It is the ABSOLUTE object list the VCMI/H3 map
# editor can place: derived from the authoritative object-template table (objects.txt in the H3 LOD),
# NOT from the corpus. Regenerate in place with `python -m vcmi_mapgen.ontology --regen`.
#
#   DECORATION -> DECORATION -> mountain -> snow -> avlmtsn3   (terrain-coupled: real terrain)
#   QUEST_PAIR -> QUEST_GATE  -> keymasterTent -> land -> red  (colour-keyed: land, leaf = colour)
#   VISIBLE    -> TOWN        -> town -> land -> avccast0      (terrain-independent: land/water)
#
# A terrain node holds its leaves as a sorted list of animation DEFs (leaf name == animation), OR a
# {colour: animation} dict for colour-keyed quest objects (leaf name == colour).

TERRAIN_NAMES = {
    0: "dirt",
    1: "sand",
    2: "grass",
    3: "snow",
    4: "swamp",
    5: "rough",
    6: "subterr",
    7: "lava",
    8: "water",
    9: "rock",
}
# Objects keyed by player colour (quest lock/key). Colour is terrain-independent, so they bucket
# under a single "land" terrain node and their leaves are named by colour (all 8 enumerated).
COLOR_KEYED_NAMES = {"BORDER_GATE", "BORDERGUARD", "KEYMASTER"}
# Types whose subtype is a meaningful identity (a town's faction) rather than a colour: the leaf is
# named by the readable subtype so the catalog branches by faction (castle/rampart/...) instead of
# dumping every faction's cryptic animation flat in one node.
SUBTYPE_KEYED_NAMES = {"TOWN"}
# objects.txt subclass -> gate/key colour (matches the avx{key,bor,bgt}NN DEF suffix /10).
GATE_COLORS = {
    0: "lblue",
    1: "green",
    2: "red",
    3: "dblue",
    4: "brown",
    5: "purple",
    6: "white",
    7: "black",
}
TREE_CACHE = os.path.join(os.path.dirname(_HERE), "out", "ontology_tree.json")

# === BEGIN GENERATED TAXONOMY (regenerate with `python -m vcmi_mapgen.ontology --regen`) ===
TAXONOMY = {
    "DECORATION": {
        "DECORATION": {
            "CACTUS": {
                "rough": ["avlca1r0", "avlca2r0"],
                "sand": ["avlca010", "avlca020", "avlca030", "avlca040", "avlca050", "avlca060", "avlca070", "avlca080", "avlca090", "avlca100", "avlca110", "avlca120", "avlca130"]
            },
            "CANYON": {
                "rough": ["avlglly0"]
            },
            "CLASS_177": {
                "rough": ["avllk1r"]
            },
            "CLASS_199": {
                "rough": ["avltro00", "avltro01", "avltro02", "avltro03", "avltro04", "avltro05", "avltro06", "avltro07", "avltro08", "avltro09", "avltro10", "avltro11", "avltro12", "avltrro0", "avltrro1", "avltrro2", "avltrro3", "avltrro4", "avltrro5", "avltrro6", "avltrro7"],
                "swamp": ["avlswt00", "avlswt01", "avlswt02", "avlswt03", "avlswt04", "avlswt05", "avlswt06", "avlswt07", "avlswt08", "avlswt09", "avlswt10", "avlswt11", "avlswt12", "avlswt13", "avlswt14", "avlswt15", "avlswt16", "avlswt17", "avlswt18", "avlswt19", "avlswtr0", "avlswtr1", "avlswtr2", "avlswtr3", "avlswtr4", "avlswtr5", "avlswtr6", "avlswtr7", "avlswtr8", "avlswtr9"]
            },
            "CLASS_206": {
                "sand": ["avlxds01", "avlxds02", "avlxds03", "avlxds04", "avlxds05", "avlxds06", "avlxds07", "avlxds08", "avlxds09", "avlxds10", "avlxds11", "avlxds12"]
            },
            "CLASS_207": {
                "dirt": ["avlxdt00", "avlxdt01", "avlxdt02", "avlxdt03", "avlxdt04", "avlxdt05", "avlxdt06", "avlxdt07", "avlxdt08", "avlxdt09", "avlxdt10", "avlxdt11"]
            },
            "CLASS_208": {
                "grass": ["avlxgr01", "avlxgr02", "avlxgr03", "avlxgr04", "avlxgr05", "avlxgr06", "avlxgr07", "avlxgr08", "avlxgr09", "avlxgr10", "avlxgr11", "avlxgr12"]
            },
            "CLASS_209": {
                "rough": ["avlxro01", "avlxro02", "avlxro03", "avlxro04", "avlxro05", "avlxro06", "avlxro07", "avlxro08", "avlxro09", "avlxro10", "avlxro11", "avlxro12"]
            },
            "CLASS_210": {
                "subterr": ["avlxsu01", "avlxsu02", "avlxsu03", "avlxsu04", "avlxsu05", "avlxsu06", "avlxsu07", "avlxsu08", "avlxsu09", "avlxsu10", "avlxsu11", "avlxsu12"]
            },
            "CLASS_211": {
                "swamp": ["avlxsw01", "avlxsw02", "avlxsw03", "avlxsw04", "avlxsw05", "avlxsw06", "avlxsw07", "avlxsw08", "avlxsw09", "avlxsw10", "avlxsw11"]
            },
            "CRATER": {
                "dirt": ["avlct1d0", "avlct2d0", "avlct3d0", "avlct4d0", "avlct5d0", "avlctrd0"],
                "grass": ["avlct1g0", "avlct2g0", "avlct3g0", "avlct4g0", "avlct5g0", "avlct6g0", "avlctrg0"],
                "lava": ["avlc10l0", "avlc11l0", "avlc12l0", "avlc13l0", "avlc14l0", "avlct1l0", "avlct2l0", "avlct3l0", "avlct4l0", "avlct5l0", "avlct6l0", "avlct7l0", "avlct8l0", "avlct9l0", "avlctrl0"],
                "rough": ["avlct1r0", "avlct2r0", "avlct3r0", "avlct4r0", "avlct5r0", "avlct6r0", "avlct7r0", "avlct8r0", "avlct9r0", "avlctrr0"],
                "sand": ["avlctds0"],
                "snow": ["avlctsn0"],
                "subterr": ["avlct1u0", "avlct2u0", "avlct3u0", "avlct4u0", "avlct5u0"],
                "swamp": ["avlctrs0"]
            },
            "DEAD_VEGETATION": {
                "lava": ["avldead0", "avldead1", "avldead2", "avldead3", "avldead4", "avldead5", "avldead6", "avldead7"],
                "snow": ["avld1sn0", "avld2sn0", "avld3sn0", "avld4sn0", "avld5sn0", "avld6sn0", "avld7sn0", "avld8sn0", "avld9sn0", "avlddsn0", "avlddsn1", "avlddsn2", "avlddsn3", "avlddsn4", "avlddsn5", "avlddsn6", "avlddsn7"],
                "subterr": ["avldead0", "avldead1", "avldead2", "avldead3", "avldead4", "avldead5", "avldead6", "avldead7"],
                "swamp": ["avldead0", "avldead1", "avldead2", "avldead3", "avldead4", "avldead5", "avldead6", "avldead7", "avldt1s0", "avldt2s0", "avldt3s0", "avlswp60", "avlswp70"]
            },
            "FLOWERS": {
                "dirt": ["avlfl1d0", "avlfl2d0", "avlfl3d0", "avlfl4d0", "avlfl5d0", "avlfl6d0", "avlfl7d0", "avlfl8d0", "avlfl9d0"],
                "grass": ["avlf01g0", "avlf02g0", "avlf03g0", "avlf04g0", "avlf05g0", "avlf06g0", "avlf07g0", "avlf08g0", "avlf09g0", "avlf10g0", "avlf11g0", "avlf12g0"]
            },
            "FROZEN_LAKE": {
                "snow": ["avlflk10", "avlflk20", "avlflk30"]
            },
            "HOLE": {
                "dirt": ["avlhold0"],
                "grass": ["avlholg0"],
                "lava": ["avlholl0"],
                "rough": ["avlholr0"],
                "sand": ["avlhlds0"],
                "snow": ["avlhlsn0"],
                "subterr": ["avlholx0"],
                "swamp": ["avlhols0"]
            },
            "KELP": {
                "water": ["avlklp10", "avlklp20"]
            },
            "LAKE": {
                "dirt": ["avllk1d0", "avllk2d0", "avllk3d0"],
                "grass": ["avllk1g0", "avllk2g0", "avllk3g0"],
                "subterr": ["avllk1u0", "avllk2u0", "avllk3u0"],
                "swamp": ["avllk1s0", "avllk2s0", "avllk3s0", "avlswp50"]
            },
            "LAVA_FLOW": {
                "lava": ["avllav20", "avllav30", "avllav40", "avllav50", "avllav60", "avllav70", "avllav80", "avllav90", "avllv100", "avllv110", "avllv120", "avllv130", "avllv140", "avllv150", "avllv160", "avllv170", "avllv180", "avllv190", "avllv200", "avllv210", "avllv220", "avllv230", "avllv240", "avllv250", "avllv260"],
                "subterr": ["avllv1u0", "avllv2u0", "avllv3u0"]
            },
            "LAVA_LAKE": {
                "lava": ["avllav10"],
                "subterr": ["avlllk10", "avlllk20"]
            },
            "LOG": {
                "dirt": ["avldlog"],
                "grass": ["avldlog"],
                "rough": ["avldlog"]
            },
            "MANDRAKE": {
                "swamp": ["avlman10", "avlman20", "avlman30", "avlman40", "avlman50"]
            },
            "MOSS": {
                "swamp": ["avlmoss0"]
            },
            "MOUND": {
                "dirt": ["avlmd1d0", "avlmd2d0"],
                "grass": ["avlmd1g0", "avlmd2g0"],
                "rough": ["avlmd1r0", "avlmd2r0", "avlmd3r0"]
            },
            "MOUNTAIN": {
                "dirt": ["avlmtdr1", "avlmtdr2", "avlmtdr3", "avlmtdr4", "avlmtdr5", "avlmtdr6", "avlmtdr7", "avlmtdr8"],
                "grass": ["avlmtgn0", "avlmtgn1", "avlmtgn2", "avlmtgn3", "avlmtgn4", "avlmtgn5", "avlmtgr1", "avlmtgr2", "avlmtgr3", "avlmtgr4", "avlmtgr5", "avlmtgr6"],
                "lava": ["avlmtvo1", "avlmtvo2", "avlmtvo3", "avlmtvo4", "avlmtvo5", "avlmtvo6"],
                "rough": ["avlmtrf1", "avlmtrf2", "avlmtrf3", "avlmtrf4", "avlmtrf5", "avlmtrf6"],
                "sand": ["avlmtds1", "avlmtds2", "avlmtds3", "avlmtds4", "avlmtds5", "avlmtds6"],
                "snow": ["avlmtsn1", "avlmtsn2", "avlmtsn3", "avlmtsn4", "avlmtsn5", "avlmtsn6"],
                "subterr": ["avlmtsb0", "avlmtsb1", "avlmtsb2", "avlmtsb3", "avlmtsb4", "avlmtsb5"],
                "swamp": ["avlmtsw1", "avlmtsw2", "avlmtsw3", "avlmtsw4", "avlmtsw5", "avlmtsw6"]
            },
            "MUSHROOMS": {
                "subterr": ["avlms010", "avlms020", "avlms030", "avlms040", "avlms050", "avlms060", "avlms070", "avlms080", "avlms090", "avlms100", "avlms110", "avlms120"]
            },
            "OAK_TREES": {
                "dirt": ["avlautr0", "avlautr1", "avlautr2", "avlautr3", "avlautr4", "avlautr5", "avlautr6", "avlautr7"],
                "grass": ["avlautr0", "avlautr1", "avlautr2", "avlautr3", "avlautr4", "avlautr5", "avlautr6", "avlautr7", "avlsptr0", "avlsptr1", "avlsptr2", "avlsptr3", "avlsptr4", "avlsptr5", "avlsptr6", "avlsptr7", "avlsptr8"],
                "swamp": ["avlsptr0", "avlsptr1", "avlsptr2", "avlsptr3", "avlsptr4", "avlsptr5", "avlsptr6", "avlsptr7", "avlsptr8"]
            },
            "OUTCROPPING": {
                "dirt": ["avloc1d0", "avloc2d0", "avloc3d0"],
                "grass": ["avloc1g0", "avloc2g0", "avloc3g0"],
                "rough": ["avloc1r0", "avloc2r0", "avloc3r0", "avloc4r0"],
                "snow": ["avlo1sn0", "avlo2sn0", "avlo3sn0"],
                "subterr": ["avloc1u0", "avloc2u0", "avloc3u0", "avloc4u0"]
            },
            "PINE_TREES": {
                "dirt": ["avlpntr0", "avlpntr1", "avlpntr2", "avlpntr3", "avlpntr4", "avlpntr5", "avlpntr6", "avlpntr7"],
                "grass": ["avlpntr0", "avlpntr1", "avlpntr2", "avlpntr3", "avlpntr4", "avlpntr5", "avlpntr6", "avlpntr7"],
                "snow": ["avlsntr0", "avlsntr1", "avlsntr2", "avlsntr3", "avlsntr4", "avlsntr5", "avlsntr6", "avlsntr7"]
            },
            "REEF": {
                "water": ["avlref10", "avlref20", "avlref30", "avlref40", "avlref50", "avlref60"]
            },
            "RIVER_DELTA": {
                "dirt": ["clrdelt1", "clrdelt2", "clrdelt3", "clrdelt4", "muddelt1", "muddelt2", "muddelt3", "muddelt4"],
                "grass": ["clrdelt1", "clrdelt2", "clrdelt3", "clrdelt4", "muddelt1", "muddelt2", "muddelt3", "muddelt4"],
                "lava": ["clrdelt1", "clrdelt2", "clrdelt3", "clrdelt4", "lavdelt1", "lavdelt2", "lavdelt3", "lavdelt4", "muddelt1", "muddelt2", "muddelt3", "muddelt4"],
                "rough": ["clrdelt1", "clrdelt2", "clrdelt3", "clrdelt4", "muddelt1", "muddelt2", "muddelt3", "muddelt4"],
                "sand": ["clrdelt1", "clrdelt2", "clrdelt3", "clrdelt4", "muddelt1", "muddelt2", "muddelt3", "muddelt4"],
                "snow": ["clrdelt1", "clrdelt2", "clrdelt3", "clrdelt4", "icedelt1", "icedelt2", "icedelt3", "icedelt4", "muddelt1", "muddelt2", "muddelt3", "muddelt4"],
                "subterr": ["clrdelt1", "clrdelt2", "clrdelt3", "clrdelt4", "muddelt1", "muddelt2", "muddelt3", "muddelt4"],
                "swamp": ["clrdelt1", "clrdelt2", "clrdelt3", "clrdelt4", "muddelt1", "muddelt2", "muddelt3", "muddelt4"]
            },
            "ROCK": {
                "dirt": ["avlrd01", "avlrd02", "avlrd04", "avlrk3d0", "avlrk5d0"],
                "grass": ["avlrg01", "avlrg02", "avlrg03", "avlrg04", "avlrg05", "avlrg06", "avlrg07", "avlrg08", "avlrg09", "avlrg10", "avlrg11"],
                "rough": ["avlbuzr0", "avlr02r0", "avlr03r0", "avlr04r0", "avlr06r0", "avlr07r0", "avlr08r0", "avlr09r0", "avlr10r0", "avlr11r0", "avlr12r0", "avlr13r0", "avlr14r0", "avlr15r0", "avlrr01", "avlrr05"],
                "snow": ["avlr1sn0", "avlr2sn0", "avlr3sn0", "avlr4sn0", "avlr5sn0", "avlr6sn0", "avlr7sn0", "avlr8sn0"],
                "subterr": ["avlr01u0", "avlr02u0", "avlr03u0", "avlr04u0", "avlr05u0", "avlr06u0", "avlr07u0", "avlr08u0", "avlr09u0", "avlr10u0", "avlr11u0", "avlr12u0", "avlr13u0", "avlr14u0", "avlr15u0", "avlr16u0", "avlstg10", "avlstg20", "avlstg30", "avlstg40", "avlstg50", "avlstg60"],
                "swamp": ["avlrk1s0", "avlrk2s0", "avlrk3s0", "avlrk4s0"],
                "water": ["avlrk1w0", "avlrk2w0", "avlrk3w0", "avlrk4w0"]
            },
            "SAND_DUNE": {
                "sand": ["avldun10", "avldun20", "avldun30"]
            },
            "SAND_PIT": {
                "sand": ["avlspit0"]
            },
            "SHRUB": {
                "dirt": ["avlsh1d0", "avlsh2d0", "avlsh3d0", "avlsh4d0", "avlsh5d0", "avlsh6d0", "avlsh7d0", "avlsh8d0"],
                "grass": ["avlsh1g0", "avlsh2g0", "avlsh3g0", "avlsh4g0", "avlsh5g0", "avlsh6g0"],
                "rough": ["avlsh1r0", "avlsh2r0", "avlsh3r0", "avlsh4r0", "avlsh5r0", "avlsh6r0", "avlsh7r0", "avlsh8r0", "avlsh9r0"],
                "snow": ["avls1sn0", "avls2sn0", "avls3sn0"],
                "swamp": ["avls01s0", "avls02s0", "avls03s0", "avls04s0", "avls05s0", "avls06s0", "avls07s0", "avls08s0", "avls09s0", "avls10s0", "avls11s0", "avlswp10", "avlswp20", "avlswp30", "avlswp40"]
            },
            "SKULL": {
                "rough": ["avlskul0"],
                "sand": ["avlskul0"]
            },
            "STUMP": {
                "dirt": ["avlstm1", "avlstm2", "avlstm3"],
                "grass": ["avlstm1", "avlstm2", "avlstm3"],
                "rough": ["avlstm1", "avlstm2", "avlstm3"],
                "snow": ["avlp1sn0", "avlp2sn0"]
            },
            "TREES": {
                "dirt": ["avltr1d0", "avltr2d0", "avltr3d0"],
                "grass": ["avlswmp0", "avlswmp1", "avlswmp2", "avlswmp3", "avlswmp4", "avlswmp5", "avlswmp6", "avlswmp7", "avltr1d0", "avltr2d0", "avltr3d0", "avlwlw10", "avlwlw20", "avlwlw30"],
                "rough": ["avlroug0", "avlroug1", "avlroug2", "avlyuc10", "avlyuc20", "avlyuc30"],
                "sand": ["avlplm10", "avlplm20", "avlplm30", "avlplm40", "avlplm50", "avlyuc10", "avlyuc20", "avlyuc30"],
                "swamp": ["avlswmp0", "avlswmp1", "avlswmp2", "avlswmp3", "avlswmp4", "avlswmp5", "avlswmp6", "avlswmp7", "avltr1d0", "avltr2d0", "avltr3d0", "avlwlw10", "avlwlw20", "avlwlw30"]
            },
            "VOLCANO": {
                "lava": ["avlvol10", "avlvol20", "avlvol30", "avlvol40", "avlvol50"]
            }
        }
    },
    "GATE": {
        "QUEST_GATE": {
            "BORDERGUARD": {
                "land": {"lblue": "avxbor00", "green": "avxbor10", "red": "avxbor20", "dblue": "avxbor30", "brown": "avxbor40", "purple": "avxbor50", "white": "avxbor60", "black": "avxbor70"}
            },
            "BORDER_GATE": {
                "land": {"lblue": "avxbgt00", "green": "avxbgt10", "red": "avxbgt20", "dblue": "avxbgt30", "brown": "avxbgt40", "purple": "avxbgt50", "white": "avxbgt60", "black": "avxbgt70"}
            },
            "QUEST_GUARD": {
                "land": ["avxbor80"]
            }
        }
    },
    "QUEST_PAIR": {
        "QUEST_GATE": {
            "KEYMASTER": {
                "land": {"lblue": "avxkey00", "green": "avxkey10", "red": "avxkey20", "dblue": "avxkey30", "brown": "avxkey40", "purple": "avxkey50", "white": "avxkey60", "black": "avxkey70"}
            },
            "SEER_HUT": {
                "land": ["avxseeb0", "avxseer0", "avxseey0"]
            }
        },
        "TRANSPORT": {
            "MONOLITH_ONE_WAY_ENTRANCE": {
                "land": ["avxmn1b0", "avxmn1r0", "avxmn1y0", "avxmn4i0", "avxmn5i0", "avxmn6i0", "avxmn7i0", "avxmn8i0"]
            },
            "MONOLITH_ONE_WAY_EXIT": {
                "land": ["avxmn4o0", "avxmn5o0", "avxmn6o0", "avxmn7o0", "avxmn8o0", "avxmx1b0", "avxmx1r0", "avxmx1y0"]
            },
            "MONOLITH_TWO_WAY": {
                "land": ["avxmn2g0", "avxmn2o0", "avxmn2p0", "avxmn4b0", "avxmn5b0", "avxmn6b0", "avxmn7b0", "avxmn8b0"]
            },
            "SUBTERRANEAN_GATE": {
                "land": ["avtcave"]
            },
            "WHIRLPOOL": {
                "water": ["avxwhrl0"]
            }
        }
    },
    "VISIBLE": {
        "BANK": {
            "CREATURE_BANK": {
                "land": ["avxbnk10", "avxbnk20", "avxbnk30", "avxbnk40", "avxbnk50", "avxbnk60", "avxbnk70"]
            },
            "CRYPT": {
                "land": ["avxgyds0", "avxgyne0", "avxgysn0"]
            },
            "DERELICT_SHIP": {
                "water": ["avadlic0"]
            },
            "DRAGON_UTOPIA": {
                "land": ["avsutop0"]
            },
            "PYRAMID": {
                "land": ["avxprmd0"]
            },
            "SHIPWRECK": {
                "water": ["avawre20", "avawrek0"]
            }
        },
        "BONUS_TEMP": {
            "BUOY": {
                "water": ["avsbuoy0"]
            },
            "FAERIE_RING": {
                "land": ["avsring0"]
            },
            "FOUNTAIN_OF_FORTUNE": {
                "land": ["avsfntn0"]
            },
            "FOUNTAIN_OF_YOUTH": {
                "land": ["avxfyth0"]
            },
            "IDOL_OF_FORTUNE": {
                "land": ["avsidol0"]
            },
            "MERMAID": {
                "water": ["avxmerm0"]
            },
            "OASIS": {
                "land": ["avxosis0"]
            },
            "RALLY_FLAG": {
                "land": ["avxrlly0"]
            },
            "SIRENS": {
                "water": ["avxsirn0"]
            },
            "STABLES": {
                "land": ["avxstbl0"]
            },
            "SWAN_POND": {
                "land": ["avsclvd0", "avsclvg0", "avsclvs0"]
            },
            "TEMPLE": {
                "land": ["avstmpl0"]
            },
            "WATERING_HOLE": {
                "land": ["avxwtrh0"]
            }
        },
        "DWELLING": {
            "CREATURE_GENERATOR1": {
                "land": ["avg2ela", "avg2ele", "avg2elf", "avg2elw", "avg2uni", "avgair0", "avgangl0", "avgazur", "avgbasl0", "avgbhld0", "avgbhmt0", "avgbkni0", "avgboar", "avgbone0", "avgcavl0", "avgcdrg", "avgcent0", "avgcros0", "avgcycl0", "avgdemn0", "avgdevl0", "avgdfly0", "avgdwrf0", "avgefre0", "avgelf0", "avgelp", "avgench", "avgerth0", "avgfbrd", "avgfdrg", "avgfire0", "avggarg0", "avggdrg0", "avggeni0", "avggnll0", "avggobl0", "avggogs0", "avggorg0", "avggrem0", "avggrff0", "avghalf", "avgharp0", "avghell0", "avghydr0", "avgimp0", "avglich0", "avglzrd0", "avgmage0", "avgmant0", "avgmdsa0", "avgmino0", "avgmonk0", "avgmumy", "avgnaga0", "avgnomd", "avgogre0", "avgorcg0", "avgpeas", "avgpega0", "avgpike0", "avgpit0", "avgpixie", "avgrdrg0", "avgrocs0", "avgrog", "avgrust", "avgshrp", "avgskel0", "avgswor0", "avgtitn0", "avgtree0", "avgtrll", "avgtrog0", "avgunic0", "avgvamp0", "avgwatr0", "avgwght0", "avgwolf0", "avgwyvn0", "avgzomb0"]
            },
            "CREATURE_GENERATOR4": {
                "land": ["avgelem0", "avggolm0"]
            },
            "RANDOM_DWELLING": {
                "land": ["avrcgen0"]
            },
            "RANDOM_DWELLING_FACTION": {
                "land": ["avrcgn00", "avrcgn01", "avrcgn02", "avrcgn03", "avrcgn04", "avrcgn05", "avrcgn06", "avrcgn07", "avrcgn08"]
            },
            "RANDOM_DWELLING_LVL": {
                "land": ["avrcgen1", "avrcgen2", "avrcgen3", "avrcgen4", "avrcgen5", "avrcgen6", "avrcgen7"]
            },
            "REFUGEE_CAMP": {
                "land": ["avgrefg0"]
            },
            "WAR_MACHINE_FACTORY": {
                "land": ["avgsieg0"]
            }
        },
        "GUARD": {
            "MONSTER": {
                "land": ["avwangl", "avwarch", "avwazure", "avwbasl", "avwbehl0", "avwbehx0", "avwbhmt0", "avwbhmx0", "avwbkni0", "avwbknx0", "avwboar", "avwbone0", "avwbonx0", "avwcdrg", "avwcent0", "avwcenx0", "avwcvlr0", "avwcvlx0", "avwcycl0", "avwcycx0", "avwddrx0", "avwdemn0", "avwdemx0", "avwdevl0", "avwdevx0", "avwdfir", "avwdfly", "avwdrag0", "avwdrax0", "avwdwrf0", "avwdwrx0", "avwefre0", "avwefrx0", "avwelfw0", "avwelfx0", "avwelma0", "avwelme0", "avwelmf0", "avwelmw0", "avwench", "avwfbird", "avwfdrg", "avwgarg0", "avwgarx0", "avwgbas", "avwgeni0", "avwgenx0", "avwglmd0", "avwglmg0", "avwgnll0", "avwgnlx0", "avwgobl0", "avwgobx0", "avwgog0", "avwgogx0", "avwgolm0", "avwgolx0", "avwgorg", "avwgorx0", "avwgrem0", "avwgrex0", "avwgrif", "avwgrix0", "avwhalf", "avwharp0", "avwharx0", "avwhcrs", "avwhoun0", "avwhoux0", "avwhydr", "avwhydx0", "avwicee", "avwimp0", "avwimpx0", "avwinfr", "avwlcrs", "avwlich0", "avwlicx0", "avwlizr", "avwlizx0", "avwmage0", "avwmagel", "avwmagx0", "avwmant0", "avwmanx0", "avwmeds", "avwmedx0", "avwmino", "avwminx0", "avwmonk", "avwmonx0", "avwmumy", "avwnaga0", "avwnagx0", "avwnomd", "avwnrg", "avwogre0", "avwogrx0", "avworc0", "avworcx0", "avwpeas", "avwpega0", "avwpegx0", "avwphx", "avwpike", "avwpikx0", "avwpitf0", "avwpitx0", "avwpixie", "avwpsye", "avwrdrg", "avwroc0", "avwrocx0", "avwrog", "avwrust", "avwsharp", "avwskel0", "avwskex0", "avwsprit", "avwstone", "avwstorm", "avwswrd0", "avwswrx0", "avwtitn0", "avwtitx0", "avwtree0", "avwtrex0", "avwtrll", "avwtrog0", "avwunic0", "avwunix0", "avwvamp0", "avwvamx0", "avwwigh", "avwwigx0", "avwwolf0", "avwwolx0", "avwwyvr", "avwwyvx0", "avwzomb0", "avwzomx0"]
            },
            "RANDOM_MONSTER": {
                "land": ["avwmrnd0"]
            },
            "RANDOM_MONSTER_L1": {
                "land": ["avwmon1"]
            },
            "RANDOM_MONSTER_L2": {
                "land": ["avwmon2"]
            },
            "RANDOM_MONSTER_L3": {
                "land": ["avwmon3"]
            },
            "RANDOM_MONSTER_L4": {
                "land": ["avwmon4"]
            },
            "RANDOM_MONSTER_L5": {
                "land": ["avwmon5"]
            },
            "RANDOM_MONSTER_L6": {
                "land": ["avwmon6"]
            },
            "RANDOM_MONSTER_L7": {
                "land": ["avwmon7"]
            }
        },
        "HERO": {
            "HERO_PLACEHOLDER": {
                "land": ["ahplace"]
            },
            "PRISON": {
                "land": ["avxprsn0"]
            }
        },
        "INFO": {
            "CARTOGRAPHER": {
                "land": ["avxmaps0", "avxmapu0", "avxmapw0"]
            },
            "COVER_OF_DARKNESS": {
                "land": ["avxcovr0"]
            },
            "DEN_OF_THIEVES": {
                "land": ["avxdend0", "avxdent"]
            },
            "EYE_OF_MAGI": {
                "land": ["avxeyem0"]
            },
            "HUT_OF_MAGI": {
                "land": ["avxhutm0"]
            },
            "OBELISK": {
                "land": ["avxoblb", "avxoblg", "avxoblk", "avxoblo", "avxoblp", "avxoblw", "avxobly"]
            },
            "OCEAN_BOTTLE": {
                "water": ["avxbttl0"]
            },
            "PILLAR_OF_FIRE": {
                "land": ["avxpllr0"]
            },
            "REDWOOD_OBSERVATORY": {
                "land": ["avxreds0", "avxredw"]
            },
            "SIGN": {
                "land": ["avxsndg0", "avxsnds0", "avxsnlv0", "avxsnsn0", "avxsnsw0"]
            }
        },
        "MANA": {
            "MAGIC_WELL": {
                "land": ["avxwelg0", "avxwelr0", "avxwlsn0"]
            }
        },
        "MINE": {
            "ABANDONED_MINE": {
                "grass": ["avxamgr"],
                "lava": ["avxamlv"],
                "rough": ["avxamro"],
                "sand": ["avxamds"],
                "snow": ["avxamsn"],
                "subterr": ["avxamsu"],
                "swamp": ["avxamsw"]
            },
            "MAGIC_SPRING": {
                "rough": ["avxmags0"]
            },
            "MINE": {
                "dirt": ["avmalch0", "avmcrdr0", "avmgedr0", "avmgodr0", "avmordr0", "avmsawd0", "avmsulf0", "avxabnd0"],
                "grass": ["avmabmg", "avmalch0", "avmcrgr0", "avmcrys0", "avmgems0", "avmgogr0", "avmgold0", "avmore0", "avmsawg0", "avmsulf0"],
                "lava": ["avmalch0", "avmcrvo0", "avmgelv0", "avmgovo0", "avmorlv0", "avmsawl0", "avmsulf0"],
                "rough": ["avmalch0", "avmcrrf0", "avmgerf0", "avmgorf0", "avmorro0", "avmsawr0", "avmsulf0"],
                "sand": ["avmalch0", "avmcrds0", "avmgerf0", "avmgods0", "avmords0", "avmsulf0", "avmswds0"],
                "snow": ["avmalcs0", "avmcrsn0", "avmgesn0", "avmgosn0", "avmorsn0", "avmsulf0", "avmswsn0"],
                "subterr": ["avmalch0", "avmcrsu0", "avmgerf0", "avmgosb0", "avmorsb0", "avmsawl0", "avmsulf0"],
                "swamp": ["avmalch0", "avmcrsw0", "avmgems0", "avmgosw0", "avmorsw0", "avmsawg0", "avmsulf0"]
            },
            "MYSTICAL_GARDEN": {
                "dirt": ["avtmyst0"],
                "grass": ["avtmyst0"],
                "swamp": ["avtmyst0"]
            },
            "WATER_WHEEL": {
                "dirt": ["avmwwhl0"],
                "grass": ["avmwwhl0"],
                "snow": ["avmwwsn0"],
                "swamp": ["avmwwhl0"]
            },
            "WINDMILL": {
                "land": ["avmwndd0"],
                "snow": ["avmwmsn0"]
            }
        },
        "RESOURCE_PILE": {
            "RANDOM_RESOURCE": {
                "land": ["avtrndm0"]
            },
            "RESOURCE": {
                "land": ["avtcrys0", "avtgems0", "avtgold0", "avtmerc0", "avtore0", "avtsulf0", "avtwood0"]
            }
        },
        "REWARD_PICKUP": {
            "ARTIFACT": {
                "land": ["ava0007", "ava0008", "ava0009", "ava0010", "ava0011", "ava0012", "ava0013", "ava0014", "ava0015", "ava0016", "ava0017", "ava0018", "ava0019", "ava0020", "ava0021", "ava0022", "ava0023", "ava0024", "ava0025", "ava0026", "ava0027", "ava0028", "ava0029", "ava0030", "ava0031", "ava0032", "ava0033", "ava0034", "ava0035", "ava0036", "ava0037", "ava0038", "ava0039", "ava0040", "ava0041", "ava0042", "ava0043", "ava0044", "ava0045", "ava0046", "ava0047", "ava0048", "ava0049", "ava0050", "ava0051", "ava0052", "ava0053", "ava0054", "ava0055", "ava0056", "ava0057", "ava0058", "ava0059", "ava0060", "ava0061", "ava0062", "ava0063", "ava0064", "ava0065", "ava0066", "ava0067", "ava0068", "ava0069", "ava0070", "ava0071", "ava0072", "ava0073", "ava0074", "ava0075", "ava0076", "ava0077", "ava0078", "ava0079", "ava0080", "ava0081", "ava0082", "ava0083", "ava0084", "ava0085", "ava0086", "ava0087", "ava0088", "ava0089", "ava0090", "ava0091", "ava0092", "ava0093", "ava0094", "ava0095", "ava0096", "ava0097", "ava0098", "ava0099", "ava0100", "ava0101", "ava0102", "ava0103", "ava0104", "ava0105", "ava0106", "ava0107", "ava0108", "ava0109", "ava0110", "ava0111", "ava0112", "ava0113", "ava0114", "ava0115", "ava0116", "ava0117", "ava0118", "ava0119", "ava0120", "ava0121", "ava0122", "ava0123", "ava0124", "ava0125", "ava0126", "ava0127", "ava0129", "ava0130", "ava0131", "ava0132", "ava0133", "ava0134", "ava0135", "ava0136", "ava0137", "ava0138", "ava0139", "ava0140", "ava0141"]
            },
            "CAMPFIRE": {
                "land": ["adcfra", "avxcfds0", "avxcflv0", "avxcfsn0"]
            },
            "CORPSE": {
                "land": ["avxskds0"]
            },
            "FLOTSAM": {
                "water": ["avaflot0"]
            },
            "LEAN_TO": {
                "land": ["avmlean0"]
            },
            "PANDORAS_BOX": {
                "land": ["ava0128"]
            },
            "RANDOM_ART": {
                "land": ["avarand"]
            },
            "RANDOM_MAJOR_ART": {
                "land": ["avarnd3"]
            },
            "RANDOM_MINOR_ART": {
                "land": ["avarnd2"]
            },
            "RANDOM_RELIC_ART": {
                "land": ["avarnd4"]
            },
            "RANDOM_TREASURE_ART": {
                "land": ["avarnd1"]
            },
            "SCHOLAR": {
                "land": ["avxschl0"]
            },
            "SEA_CHEST": {
                "water": ["avxccht0"]
            },
            "SHIPWRECK_SURVIVOR": {
                "water": ["avasurv0"]
            },
            "SPELL_SCROLL": {
                "land": ["ava0001"]
            },
            "TREASURE_CHEST": {
                "land": ["avtchst0"]
            },
            "WAGON": {
                "land": ["avtwagn0"]
            },
            "WARRIORS_TOMB": {
                "land": ["avxtomb0"]
            }
        },
        "SPECIAL": {
            "ALTAR_OF_SACRIFICE": {
                "land": ["avxaltar"]
            },
            "BLACK_MARKET": {
                "land": ["avxmktb0"]
            },
            "EVENT": {
                "land": ["avzevnt0"]
            },
            "FREELANCERS_GUILD": {
                "land": ["avxfgld"]
            },
            "GARRISON": {
                "land": ["avcgar10", "avcgar20"]
            },
            "GARRISON2": {
                "land": ["avcvgarm", "avcvgr"]
            },
            "GRAIL": {
                "land": ["avzgrail"]
            },
            "SANCTUARY": {
                "land": ["avxsanc0"]
            },
            "TAVERN": {
                "land": ["avxtvrn0"]
            },
            "TRADING_POST": {
                "land": ["avxpost0", "avxpstr0"]
            },
            "TRADING_POST_SNOW": {
                "land": ["avxpssn"]
            }
        },
        "SPELL_SKILL": {
            "SHRINE_OF_MAGIC_GESTURE": {
                "land": ["avxl2sh0"]
            },
            "SHRINE_OF_MAGIC_INCANTATION": {
                "land": ["avxl1sh0"]
            },
            "SHRINE_OF_MAGIC_THOUGHT": {
                "land": ["avxl3sh0"]
            },
            "UNIVERSITY": {
                "land": ["avsuniv0"]
            },
            "WITCH_HUT": {
                "land": ["avswtch0"]
            }
        },
        "STAT_PERMANENT": {
            "ARENA": {
                "land": ["avsarna0"]
            },
            "GARDEN_OF_REVELATION": {
                "land": ["avsgrdn0"]
            },
            "HILL_FORT": {
                "land": ["avxhild0", "avxhilg0"]
            },
            "LEARNING_STONE": {
                "land": ["avsgzbo0"]
            },
            "LIBRARY_OF_ENLIGHTENMENT": {
                "land": ["avslibr0"]
            },
            "MARLETTO_TOWER": {
                "land": ["avsmarl"]
            },
            "MERCENARY_CAMP": {
                "land": ["avsmerc0"]
            },
            "SCHOOL_OF_MAGIC": {
                "land": ["avsschm0"]
            },
            "SCHOOL_OF_WAR": {
                "land": ["avswar20"]
            },
            "STAR_AXIS": {
                "land": ["avsaxis0"]
            },
            "TREE_OF_KNOWLEDGE": {
                "land": ["avxtrek0"]
            }
        },
        "TERRAIN_MODIFIER": {
            "CLOVER_FIELD": {
                "land": ["avxcf0", "avxcf1", "avxcf2", "avxcf3", "avxcf4", "avxcf5", "avxcf6", "avxcf7"]
            },
            "CURSED_GROUND1": {
                "land": ["avxcrsd0"]
            },
            "CURSED_GROUND2": {
                "land": ["avxcg1", "avxcg2", "avxcg3", "avxcg4", "avxcg5", "avxcg6", "avxcg7"]
            },
            "EVIL_FOG": {
                "land": ["avxef0", "avxef1", "avxef2", "avxef3", "avxef4", "avxef5", "avxef6", "avxef7"]
            },
            "FAVORABLE_WINDS": {
                "water": ["avxfw0", "avxfw1", "avxfw2", "avxfw3", "avxfw4", "avxfw5", "avxfw6", "avxfw7"]
            },
            "FIERY_FIELDS": {
                "land": ["avxff0", "avxff1", "avxff2", "avxff3", "avxff4", "avxff5", "avxff6", "avxff7"]
            },
            "HOLY_GROUNDS": {
                "land": ["avxhg0", "avxhg1", "avxhg2", "avxhg3", "avxhg4", "avxhg5", "avxhg6", "avxhg7"]
            },
            "LUCID_POOLS": {
                "land": ["avxlp0", "avxlp1", "avxlp2", "avxlp3", "avxlp4", "avxlp5", "avxlp6", "avxlp7"]
            },
            "MAGIC_CLOUDS": {
                "land": ["avxmc0", "avxmc1", "avxmc2", "avxmc3", "avxmc4", "avxmc5", "avxmc6", "avxmc7"]
            },
            "MAGIC_PLAINS1": {
                "land": ["avxplns0"]
            },
            "MAGIC_PLAINS2": {
                "land": ["avxmp1", "avxmp2", "avxmp3", "avxmp4", "avxmp5", "avxmp6", "avxmp7"]
            },
            "ROCKLANDS": {
                "land": ["avxrk0", "avxrk1", "avxrk2", "avxrk3", "avxrk4", "avxrk5", "avxrk6", "avxrk7"]
            }
        },
        "TOWN": {
            "RANDOM_TOWN": {
                "land": ["avcranx0"]
            },
            "TOWN": {
                "land": {"castle": "avccasx0", "rampart": "avcramx0", "tower": "avctowx0", "inferno": "avcinfx0", "necropolis": "avcnecx0", "dungeon": "avcdunx0", "stronghold": "avcstrx0", "fortress": "avcftrx0", "conflux": "avchforx"}
            }
        },
        "WATER_TRANSPORT": {
            "BOAT": {
                "water": ["avxboat0", "avxboat1", "avxboat2"]
            },
            "LIGHTHOUSE": {
                "dirt": ["avxlths0"],
                "grass": ["avxlths0"],
                "lava": ["avxlths0"],
                "rough": ["avxlths0"],
                "sand": ["avxlths0"],
                "snow": ["avxlths0"],
                "swamp": ["avxlths0"]
            },
            "SHIPYARD": {
                "land": ["avxshyd0"]
            }
        }
    }
}
# === END GENERATED TAXONOMY ===

# Per-animation placement metadata (footprint mask + class/subclass) so the ontology is
# self-sufficient for tile placement and `.vmap` writing — no corpus needed. Keyed by the
# (lowercase) animation DEF; mask is the B/A/V row-strings (obj_resolve.mask_cells semantics),
# decoded from the authoritative objects.txt passability/triggers bitfields. Regenerate with
# `python -m vcmi_mapgen.ontology --regen`.
# === BEGIN GENERATED LEAF_META ===
LEAF_META = {
    "adcfra": {"cls": 12, "sub": 0, "mask": ["A"]},
    "ahplace": {"cls": 214, "sub": 0, "mask": ["A"]},
    "ava0001": {"cls": 93, "sub": 0, "mask": ["A"]},
    "ava0007": {"cls": 5, "sub": 7, "mask": ["A"]},
    "ava0008": {"cls": 5, "sub": 8, "mask": ["A"]},
    "ava0009": {"cls": 5, "sub": 9, "mask": ["A"]},
    "ava0010": {"cls": 5, "sub": 10, "mask": ["A"]},
    "ava0011": {"cls": 5, "sub": 11, "mask": ["A"]},
    "ava0012": {"cls": 5, "sub": 12, "mask": ["A"]},
    "ava0013": {"cls": 5, "sub": 13, "mask": ["A"]},
    "ava0014": {"cls": 5, "sub": 14, "mask": ["A"]},
    "ava0015": {"cls": 5, "sub": 15, "mask": ["A"]},
    "ava0016": {"cls": 5, "sub": 16, "mask": ["A"]},
    "ava0017": {"cls": 5, "sub": 17, "mask": ["A"]},
    "ava0018": {"cls": 5, "sub": 18, "mask": ["A"]},
    "ava0019": {"cls": 5, "sub": 19, "mask": ["A"]},
    "ava0020": {"cls": 5, "sub": 20, "mask": ["A"]},
    "ava0021": {"cls": 5, "sub": 21, "mask": ["A"]},
    "ava0022": {"cls": 5, "sub": 22, "mask": ["A"]},
    "ava0023": {"cls": 5, "sub": 23, "mask": ["A"]},
    "ava0024": {"cls": 5, "sub": 24, "mask": ["A"]},
    "ava0025": {"cls": 5, "sub": 25, "mask": ["A"]},
    "ava0026": {"cls": 5, "sub": 26, "mask": ["A"]},
    "ava0027": {"cls": 5, "sub": 27, "mask": ["A"]},
    "ava0028": {"cls": 5, "sub": 28, "mask": ["A"]},
    "ava0029": {"cls": 5, "sub": 29, "mask": ["A"]},
    "ava0030": {"cls": 5, "sub": 30, "mask": ["A"]},
    "ava0031": {"cls": 5, "sub": 31, "mask": ["A"]},
    "ava0032": {"cls": 5, "sub": 32, "mask": ["A"]},
    "ava0033": {"cls": 5, "sub": 33, "mask": ["A"]},
    "ava0034": {"cls": 5, "sub": 34, "mask": ["A"]},
    "ava0035": {"cls": 5, "sub": 35, "mask": ["A"]},
    "ava0036": {"cls": 5, "sub": 36, "mask": ["A"]},
    "ava0037": {"cls": 5, "sub": 37, "mask": ["A"]},
    "ava0038": {"cls": 5, "sub": 38, "mask": ["A"]},
    "ava0039": {"cls": 5, "sub": 39, "mask": ["A"]},
    "ava0040": {"cls": 5, "sub": 40, "mask": ["A"]},
    "ava0041": {"cls": 5, "sub": 41, "mask": ["A"]},
    "ava0042": {"cls": 5, "sub": 42, "mask": ["A"]},
    "ava0043": {"cls": 5, "sub": 43, "mask": ["A"]},
    "ava0044": {"cls": 5, "sub": 44, "mask": ["A"]},
    "ava0045": {"cls": 5, "sub": 45, "mask": ["A"]},
    "ava0046": {"cls": 5, "sub": 46, "mask": ["A"]},
    "ava0047": {"cls": 5, "sub": 47, "mask": ["A"]},
    "ava0048": {"cls": 5, "sub": 48, "mask": ["A"]},
    "ava0049": {"cls": 5, "sub": 49, "mask": ["A"]},
    "ava0050": {"cls": 5, "sub": 50, "mask": ["A"]},
    "ava0051": {"cls": 5, "sub": 51, "mask": ["A"]},
    "ava0052": {"cls": 5, "sub": 52, "mask": ["A"]},
    "ava0053": {"cls": 5, "sub": 53, "mask": ["A"]},
    "ava0054": {"cls": 5, "sub": 54, "mask": ["A"]},
    "ava0055": {"cls": 5, "sub": 55, "mask": ["A"]},
    "ava0056": {"cls": 5, "sub": 56, "mask": ["A"]},
    "ava0057": {"cls": 5, "sub": 57, "mask": ["A"]},
    "ava0058": {"cls": 5, "sub": 58, "mask": ["A"]},
    "ava0059": {"cls": 5, "sub": 59, "mask": ["A"]},
    "ava0060": {"cls": 5, "sub": 60, "mask": ["A"]},
    "ava0061": {"cls": 5, "sub": 61, "mask": ["A"]},
    "ava0062": {"cls": 5, "sub": 62, "mask": ["A"]},
    "ava0063": {"cls": 5, "sub": 63, "mask": ["A"]},
    "ava0064": {"cls": 5, "sub": 64, "mask": ["A"]},
    "ava0065": {"cls": 5, "sub": 65, "mask": ["A"]},
    "ava0066": {"cls": 5, "sub": 66, "mask": ["A"]},
    "ava0067": {"cls": 5, "sub": 67, "mask": ["A"]},
    "ava0068": {"cls": 5, "sub": 68, "mask": ["A"]},
    "ava0069": {"cls": 5, "sub": 69, "mask": ["A"]},
    "ava0070": {"cls": 5, "sub": 70, "mask": ["A"]},
    "ava0071": {"cls": 5, "sub": 71, "mask": ["A"]},
    "ava0072": {"cls": 5, "sub": 72, "mask": ["A"]},
    "ava0073": {"cls": 5, "sub": 73, "mask": ["A"]},
    "ava0074": {"cls": 5, "sub": 74, "mask": ["A"]},
    "ava0075": {"cls": 5, "sub": 75, "mask": ["A"]},
    "ava0076": {"cls": 5, "sub": 76, "mask": ["A"]},
    "ava0077": {"cls": 5, "sub": 77, "mask": ["A"]},
    "ava0078": {"cls": 5, "sub": 78, "mask": ["A"]},
    "ava0079": {"cls": 5, "sub": 79, "mask": ["A"]},
    "ava0080": {"cls": 5, "sub": 80, "mask": ["A"]},
    "ava0081": {"cls": 5, "sub": 81, "mask": ["A"]},
    "ava0082": {"cls": 5, "sub": 82, "mask": ["A"]},
    "ava0083": {"cls": 5, "sub": 83, "mask": ["A"]},
    "ava0084": {"cls": 5, "sub": 84, "mask": ["A"]},
    "ava0085": {"cls": 5, "sub": 85, "mask": ["A"]},
    "ava0086": {"cls": 5, "sub": 86, "mask": ["A"]},
    "ava0087": {"cls": 5, "sub": 87, "mask": ["A"]},
    "ava0088": {"cls": 5, "sub": 88, "mask": ["A"]},
    "ava0089": {"cls": 5, "sub": 89, "mask": ["A"]},
    "ava0090": {"cls": 5, "sub": 90, "mask": ["A"]},
    "ava0091": {"cls": 5, "sub": 91, "mask": ["A"]},
    "ava0092": {"cls": 5, "sub": 92, "mask": ["A"]},
    "ava0093": {"cls": 5, "sub": 93, "mask": ["A"]},
    "ava0094": {"cls": 5, "sub": 94, "mask": ["A"]},
    "ava0095": {"cls": 5, "sub": 95, "mask": ["A"]},
    "ava0096": {"cls": 5, "sub": 96, "mask": ["A"]},
    "ava0097": {"cls": 5, "sub": 97, "mask": ["A"]},
    "ava0098": {"cls": 5, "sub": 98, "mask": ["A"]},
    "ava0099": {"cls": 5, "sub": 99, "mask": ["A"]},
    "ava0100": {"cls": 5, "sub": 100, "mask": ["A"]},
    "ava0101": {"cls": 5, "sub": 101, "mask": ["A"]},
    "ava0102": {"cls": 5, "sub": 102, "mask": ["A"]},
    "ava0103": {"cls": 5, "sub": 103, "mask": ["A"]},
    "ava0104": {"cls": 5, "sub": 104, "mask": ["A"]},
    "ava0105": {"cls": 5, "sub": 105, "mask": ["A"]},
    "ava0106": {"cls": 5, "sub": 106, "mask": ["A"]},
    "ava0107": {"cls": 5, "sub": 107, "mask": ["A"]},
    "ava0108": {"cls": 5, "sub": 108, "mask": ["A"]},
    "ava0109": {"cls": 5, "sub": 109, "mask": ["A"]},
    "ava0110": {"cls": 5, "sub": 110, "mask": ["A"]},
    "ava0111": {"cls": 5, "sub": 111, "mask": ["A"]},
    "ava0112": {"cls": 5, "sub": 112, "mask": ["A"]},
    "ava0113": {"cls": 5, "sub": 113, "mask": ["A"]},
    "ava0114": {"cls": 5, "sub": 114, "mask": ["A"]},
    "ava0115": {"cls": 5, "sub": 115, "mask": ["A"]},
    "ava0116": {"cls": 5, "sub": 116, "mask": ["A"]},
    "ava0117": {"cls": 5, "sub": 117, "mask": ["A"]},
    "ava0118": {"cls": 5, "sub": 118, "mask": ["A"]},
    "ava0119": {"cls": 5, "sub": 119, "mask": ["A"]},
    "ava0120": {"cls": 5, "sub": 120, "mask": ["A"]},
    "ava0121": {"cls": 5, "sub": 121, "mask": ["A"]},
    "ava0122": {"cls": 5, "sub": 122, "mask": ["A"]},
    "ava0123": {"cls": 5, "sub": 123, "mask": ["A"]},
    "ava0124": {"cls": 5, "sub": 124, "mask": ["A"]},
    "ava0125": {"cls": 5, "sub": 125, "mask": ["A"]},
    "ava0126": {"cls": 5, "sub": 126, "mask": ["A"]},
    "ava0127": {"cls": 5, "sub": 127, "mask": ["A"]},
    "ava0128": {"cls": 6, "sub": 0, "mask": ["A"]},
    "ava0129": {"cls": 5, "sub": 128, "mask": ["A"]},
    "ava0130": {"cls": 5, "sub": 129, "mask": ["A"]},
    "ava0131": {"cls": 5, "sub": 130, "mask": ["A"]},
    "ava0132": {"cls": 5, "sub": 131, "mask": ["A"]},
    "ava0133": {"cls": 5, "sub": 132, "mask": ["A"]},
    "ava0134": {"cls": 5, "sub": 133, "mask": ["A"]},
    "ava0135": {"cls": 5, "sub": 134, "mask": ["A"]},
    "ava0136": {"cls": 5, "sub": 135, "mask": ["A"]},
    "ava0137": {"cls": 5, "sub": 136, "mask": ["A"]},
    "ava0138": {"cls": 5, "sub": 137, "mask": ["A"]},
    "ava0139": {"cls": 5, "sub": 138, "mask": ["A"]},
    "ava0140": {"cls": 5, "sub": 139, "mask": ["A"]},
    "ava0141": {"cls": 5, "sub": 140, "mask": ["A"]},
    "avadlic0": {"cls": 24, "sub": 0, "mask": ["BX"]},
    "avaflot0": {"cls": 29, "sub": 0, "mask": ["A"]},
    "avarand": {"cls": 65, "sub": 0, "mask": ["A"]},
    "avarnd1": {"cls": 66, "sub": 0, "mask": ["A"]},
    "avarnd2": {"cls": 67, "sub": 0, "mask": ["A"]},
    "avarnd3": {"cls": 68, "sub": 0, "mask": ["A"]},
    "avarnd4": {"cls": 69, "sub": 0, "mask": ["A"]},
    "avasurv0": {"cls": 86, "sub": 0, "mask": ["A"]},
    "avawre20": {"cls": 85, "sub": 0, "mask": ["XB"]},
    "avawrek0": {"cls": 85, "sub": 0, "mask": ["BX"]},
    "avccasx0": {"cls": 98, "sub": 0, "mask": ["VBBBV", "BBBBB", "BBXBB"]},
    "avcdunx0": {"cls": 98, "sub": 5, "mask": ["VBBBV", "BBBBB", "BBXBB"]},
    "avcftrx0": {"cls": 98, "sub": 7, "mask": ["VBBBV", "BBBBB", "BBXBB"]},
    "avcgar10": {"cls": 33, "sub": 0, "mask": ["BXB"]},
    "avcgar20": {"cls": 33, "sub": 1, "mask": ["BXB"]},
    "avchforx": {"cls": 98, "sub": 8, "mask": ["VBBBV", "BBBBB", "BBXBB"]},
    "avcinfx0": {"cls": 98, "sub": 3, "mask": ["VBBBV", "BBBBB", "BBXBB"]},
    "avcnecx0": {"cls": 98, "sub": 4, "mask": ["VBBBV", "BBBBB", "BBXBB"]},
    "avcramx0": {"cls": 98, "sub": 1, "mask": ["VBBBV", "BBBBB", "BBXBB"]},
    "avcranx0": {"cls": 77, "sub": 0, "mask": ["VBBBV", "BBBBB", "BBXBB"]},
    "avcstrx0": {"cls": 98, "sub": 6, "mask": ["VBBBV", "BBBBB", "BBXBB"]},
    "avctowx0": {"cls": 98, "sub": 2, "mask": ["VBBBV", "BBBBB", "BBXBB"]},
    "avcvgarm": {"cls": 219, "sub": 1, "mask": ["B", "X", "B"]},
    "avcvgr": {"cls": 219, "sub": 0, "mask": ["B", "X", "B"]},
    "avg2ela": {"cls": 17, "sub": 69, "mask": ["XB"]},
    "avg2ele": {"cls": 17, "sub": 70, "mask": ["XB"]},
    "avg2elf": {"cls": 17, "sub": 71, "mask": ["XB"]},
    "avg2elw": {"cls": 17, "sub": 72, "mask": ["XB"]},
    "avg2uni": {"cls": 17, "sub": 68, "mask": ["XB"]},
    "avgair0": {"cls": 17, "sub": 7, "mask": ["BXB"]},
    "avgangl0": {"cls": 17, "sub": 8, "mask": ["XB"]},
    "avgazur": {"cls": 17, "sub": 62, "mask": ["XB"]},
    "avgbasl0": {"cls": 17, "sub": 0, "mask": ["XB"]},
    "avgbhld0": {"cls": 17, "sub": 2, "mask": ["BX"]},
    "avgbhmt0": {"cls": 17, "sub": 1, "mask": ["XB"]},
    "avgbkni0": {"cls": 17, "sub": 3, "mask": ["XB"]},
    "avgboar": {"cls": 17, "sub": 75, "mask": ["BB", "XB"]},
    "avgbone0": {"cls": 17, "sub": 4, "mask": ["BX"]},
    "avgcavl0": {"cls": 17, "sub": 5, "mask": ["XB"]},
    "avgcdrg": {"cls": 17, "sub": 63, "mask": ["XB"]},
    "avgcent0": {"cls": 17, "sub": 6, "mask": ["XB"]},
    "avgcros0": {"cls": 17, "sub": 57, "mask": ["BX"]},
    "avgcycl0": {"cls": 17, "sub": 9, "mask": ["XB"]},
    "avgdemn0": {"cls": 17, "sub": 37, "mask": ["XB"]},
    "avgdevl0": {"cls": 17, "sub": 10, "mask": ["XB"]},
    "avgdfly0": {"cls": 17, "sub": 11, "mask": ["XB"]},
    "avgdwrf0": {"cls": 17, "sub": 12, "mask": ["BX"]},
    "avgefre0": {"cls": 17, "sub": 14, "mask": ["BX"]},
    "avgelem0": {"cls": 20, "sub": 0, "mask": ["BBB", "BXB"]},
    "avgelf0": {"cls": 17, "sub": 15, "mask": ["BX"]},
    "avgelp": {"cls": 17, "sub": 60, "mask": ["XB"]},
    "avgench": {"cls": 17, "sub": 66, "mask": ["XB"]},
    "avgerth0": {"cls": 17, "sub": 13, "mask": ["BXB"]},
    "avgfbrd": {"cls": 17, "sub": 61, "mask": ["XB"]},
    "avgfdrg": {"cls": 17, "sub": 64, "mask": ["XB"]},
    "avgfire0": {"cls": 17, "sub": 16, "mask": ["BXB"]},
    "avggarg0": {"cls": 17, "sub": 17, "mask": ["XB"]},
    "avggdrg0": {"cls": 17, "sub": 24, "mask": ["XB"]},
    "avggeni0": {"cls": 17, "sub": 18, "mask": ["XB"]},
    "avggnll0": {"cls": 17, "sub": 20, "mask": ["XB"]},
    "avggobl0": {"cls": 17, "sub": 21, "mask": ["XB"]},
    "avggogs0": {"cls": 17, "sub": 22, "mask": ["BX"]},
    "avggolm0": {"cls": 20, "sub": 1, "mask": ["XB"]},
    "avggorg0": {"cls": 17, "sub": 23, "mask": ["XB"]},
    "avggrem0": {"cls": 17, "sub": 43, "mask": ["XB"]},
    "avggrff0": {"cls": 17, "sub": 25, "mask": ["XB"]},
    "avghalf": {"cls": 17, "sub": 73, "mask": ["XB"]},
    "avgharp0": {"cls": 17, "sub": 26, "mask": ["XB"]},
    "avghell0": {"cls": 17, "sub": 27, "mask": ["XB"]},
    "avghydr0": {"cls": 17, "sub": 28, "mask": ["BX"]},
    "avgimp0": {"cls": 17, "sub": 29, "mask": ["XB"]},
    "avglich0": {"cls": 17, "sub": 52, "mask": ["XB"]},
    "avglzrd0": {"cls": 17, "sub": 30, "mask": ["XB"]},
    "avgmage0": {"cls": 17, "sub": 31, "mask": ["XB"]},
    "avgmant0": {"cls": 17, "sub": 32, "mask": ["XB"]},
    "avgmdsa0": {"cls": 17, "sub": 33, "mask": ["XB"]},
    "avgmino0": {"cls": 17, "sub": 34, "mask": ["BX"]},
    "avgmonk0": {"cls": 17, "sub": 35, "mask": ["XB"]},
    "avgmumy": {"cls": 17, "sub": 76, "mask": ["BB", "BX"]},
    "avgnaga0": {"cls": 17, "sub": 36, "mask": ["BB", "XB"]},
    "avgnomd": {"cls": 17, "sub": 77, "mask": ["XB"]},
    "avgogre0": {"cls": 17, "sub": 38, "mask": ["BB", "XB"]},
    "avgorcg0": {"cls": 17, "sub": 39, "mask": ["XB"]},
    "avgpeas": {"cls": 17, "sub": 74, "mask": ["XB"]},
    "avgpega0": {"cls": 17, "sub": 50, "mask": ["XB"]},
    "avgpike0": {"cls": 17, "sub": 56, "mask": ["VB", "XB"]},
    "avgpit0": {"cls": 17, "sub": 40, "mask": ["XB"]},
    "avgpixie": {"cls": 17, "sub": 59, "mask": ["XB"]},
    "avgrdrg0": {"cls": 17, "sub": 41, "mask": ["BX"]},
    "avgrefg0": {"cls": 78, "sub": 0, "mask": ["XB"]},
    "avgrocs0": {"cls": 17, "sub": 42, "mask": ["XB"]},
    "avgrog": {"cls": 17, "sub": 78, "mask": ["XB"]},
    "avgrust": {"cls": 17, "sub": 65, "mask": ["XB"]},
    "avgshrp": {"cls": 17, "sub": 67, "mask": ["XB"]},
    "avgsieg0": {"cls": 106, "sub": 0, "mask": ["BX"]},
    "avgskel0": {"cls": 17, "sub": 54, "mask": ["XB"]},
    "avgswor0": {"cls": 17, "sub": 58, "mask": ["XB"]},
    "avgtitn0": {"cls": 17, "sub": 44, "mask": ["VB", "XB"]},
    "avgtree0": {"cls": 17, "sub": 45, "mask": ["XB"]},
    "avgtrll": {"cls": 17, "sub": 79, "mask": ["VB", "XB"]},
    "avgtrog0": {"cls": 17, "sub": 46, "mask": ["XB"]},
    "avgunic0": {"cls": 17, "sub": 51, "mask": ["VBB", "BXB"]},
    "avgvamp0": {"cls": 17, "sub": 53, "mask": ["BX"]},
    "avgwatr0": {"cls": 17, "sub": 47, "mask": ["VBV", "BXB"]},
    "avgwght0": {"cls": 17, "sub": 48, "mask": ["BX"]},
    "avgwolf0": {"cls": 17, "sub": 19, "mask": ["VB", "XB"]},
    "avgwyvn0": {"cls": 17, "sub": 49, "mask": ["BX"]},
    "avgzomb0": {"cls": 17, "sub": 55, "mask": ["XB"]},
    "avlautr0": {"cls": 135, "sub": 0, "mask": ["B"]},
    "avlautr1": {"cls": 135, "sub": 0, "mask": ["B"]},
    "avlautr2": {"cls": 135, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlautr3": {"cls": 135, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlautr4": {"cls": 135, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlautr5": {"cls": 135, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlautr6": {"cls": 135, "sub": 0, "mask": ["VBB", "BBB", "BBV"]},
    "avlautr7": {"cls": 135, "sub": 0, "mask": ["BBV", "BBB", "VBB"]},
    "avlbuzr0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlc10l0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlc11l0": {"cls": 118, "sub": 0, "mask": ["BB", "BV"]},
    "avlc12l0": {"cls": 118, "sub": 0, "mask": ["B"]},
    "avlc13l0": {"cls": 118, "sub": 0, "mask": ["B"]},
    "avlc14l0": {"cls": 118, "sub": 0, "mask": ["B"]},
    "avlca010": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca020": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca030": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca040": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca050": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca060": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca070": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca080": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca090": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca100": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca110": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca120": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca130": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca1r0": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlca2r0": {"cls": 116, "sub": 0, "mask": ["B"]},
    "avlct1d0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlct1g0": {"cls": 118, "sub": 0, "mask": ["BBBB", "BBBV"]},
    "avlct1l0": {"cls": 118, "sub": 0, "mask": ["BBBB", "BBBB"]},
    "avlct1r0": {"cls": 118, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlct1u0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlct2d0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlct2g0": {"cls": 118, "sub": 0, "mask": ["BBB", "BBB", "BBB"]},
    "avlct2l0": {"cls": 118, "sub": 0, "mask": ["BB", "BB", "BB", "BB"]},
    "avlct2r0": {"cls": 118, "sub": 0, "mask": ["BB", "BB", "VB"]},
    "avlct2u0": {"cls": 118, "sub": 0, "mask": ["B"]},
    "avlct3d0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlct3g0": {"cls": 118, "sub": 0, "mask": ["BB", "BB"]},
    "avlct3l0": {"cls": 118, "sub": 0, "mask": ["BB", "BB"]},
    "avlct3r0": {"cls": 118, "sub": 0, "mask": ["BBB"]},
    "avlct3u0": {"cls": 118, "sub": 0, "mask": ["B", "B"]},
    "avlct4d0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlct4g0": {"cls": 118, "sub": 0, "mask": ["B"]},
    "avlct4l0": {"cls": 118, "sub": 0, "mask": ["BB", "VB"]},
    "avlct4r0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlct4u0": {"cls": 118, "sub": 0, "mask": ["BB", "BB"]},
    "avlct5d0": {"cls": 118, "sub": 0, "mask": ["BB", "BB"]},
    "avlct5g0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlct5l0": {"cls": 118, "sub": 0, "mask": ["B", "B"]},
    "avlct5r0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlct5u0": {"cls": 118, "sub": 0, "mask": ["B"]},
    "avlct6g0": {"cls": 118, "sub": 0, "mask": ["B"]},
    "avlct6l0": {"cls": 118, "sub": 0, "mask": ["B", "B"]},
    "avlct6r0": {"cls": 118, "sub": 0, "mask": ["BB", "BB"]},
    "avlct7l0": {"cls": 118, "sub": 0, "mask": ["B", "B"]},
    "avlct7r0": {"cls": 118, "sub": 0, "mask": ["B"]},
    "avlct8l0": {"cls": 118, "sub": 0, "mask": ["B", "B"]},
    "avlct8r0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlct9l0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlct9r0": {"cls": 118, "sub": 0, "mask": ["BB", "BB"]},
    "avlctds0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlctrd0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlctrg0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlctrl0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlctrr0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlctrs0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avlctsn0": {"cls": 118, "sub": 0, "mask": ["BB"]},
    "avld1sn0": {"cls": 119, "sub": 0, "mask": ["BB"]},
    "avld2sn0": {"cls": 119, "sub": 0, "mask": ["BBB"]},
    "avld3sn0": {"cls": 119, "sub": 0, "mask": ["B"]},
    "avld4sn0": {"cls": 119, "sub": 0, "mask": ["BB"]},
    "avld5sn0": {"cls": 119, "sub": 0, "mask": ["B"]},
    "avld6sn0": {"cls": 119, "sub": 0, "mask": ["B"]},
    "avld7sn0": {"cls": 119, "sub": 0, "mask": ["B"]},
    "avld8sn0": {"cls": 119, "sub": 0, "mask": ["B"]},
    "avld9sn0": {"cls": 119, "sub": 0, "mask": ["B"]},
    "avlddsn0": {"cls": 119, "sub": 0, "mask": ["B"]},
    "avlddsn1": {"cls": 119, "sub": 0, "mask": ["B"]},
    "avlddsn2": {"cls": 119, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlddsn3": {"cls": 119, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlddsn4": {"cls": 119, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlddsn5": {"cls": 119, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlddsn6": {"cls": 119, "sub": 0, "mask": ["VBB", "BBB", "BBV"]},
    "avlddsn7": {"cls": 119, "sub": 0, "mask": ["BBV", "BBB", "VBB"]},
    "avldead0": {"cls": 119, "sub": 0, "mask": ["B"]},
    "avldead1": {"cls": 119, "sub": 0, "mask": ["B"]},
    "avldead2": {"cls": 119, "sub": 0, "mask": ["VBB", "BBV"]},
    "avldead3": {"cls": 119, "sub": 0, "mask": ["BBV", "VBB"]},
    "avldead4": {"cls": 119, "sub": 0, "mask": ["VBB", "BBV"]},
    "avldead5": {"cls": 119, "sub": 0, "mask": ["BBV", "VBB"]},
    "avldead6": {"cls": 119, "sub": 0, "mask": ["VBB", "BBB", "BBV"]},
    "avldead7": {"cls": 119, "sub": 0, "mask": ["BBV", "BBB", "VBB"]},
    "avldlog": {"cls": 130, "sub": 0, "mask": ["B"]},
    "avldt1s0": {"cls": 119, "sub": 0, "mask": ["BB"]},
    "avldt2s0": {"cls": 119, "sub": 0, "mask": ["BB"]},
    "avldt3s0": {"cls": 119, "sub": 0, "mask": ["BB", "BB"]},
    "avldun10": {"cls": 148, "sub": 0, "mask": ["BB"]},
    "avldun20": {"cls": 148, "sub": 0, "mask": ["BB"]},
    "avldun30": {"cls": 148, "sub": 0, "mask": ["BBB"]},
    "avlf01g0": {"cls": 120, "sub": 0, "mask": ["B"]},
    "avlf02g0": {"cls": 120, "sub": 0, "mask": ["BBB"]},
    "avlf03g0": {"cls": 120, "sub": 0, "mask": ["BBB"]},
    "avlf04g0": {"cls": 120, "sub": 0, "mask": ["BBB"]},
    "avlf05g0": {"cls": 120, "sub": 0, "mask": ["BBB"]},
    "avlf06g0": {"cls": 120, "sub": 0, "mask": ["BB"]},
    "avlf07g0": {"cls": 120, "sub": 0, "mask": ["BB"]},
    "avlf08g0": {"cls": 120, "sub": 0, "mask": ["BB"]},
    "avlf09g0": {"cls": 120, "sub": 0, "mask": ["B"]},
    "avlf10g0": {"cls": 120, "sub": 0, "mask": ["BB"]},
    "avlf11g0": {"cls": 120, "sub": 0, "mask": ["B"]},
    "avlf12g0": {"cls": 120, "sub": 0, "mask": ["B"]},
    "avlfl1d0": {"cls": 120, "sub": 0, "mask": ["BB"]},
    "avlfl2d0": {"cls": 120, "sub": 0, "mask": ["BB"]},
    "avlfl3d0": {"cls": 120, "sub": 0, "mask": ["BB"]},
    "avlfl4d0": {"cls": 120, "sub": 0, "mask": ["BB"]},
    "avlfl5d0": {"cls": 120, "sub": 0, "mask": ["BBB"]},
    "avlfl6d0": {"cls": 120, "sub": 0, "mask": ["B"]},
    "avlfl7d0": {"cls": 120, "sub": 0, "mask": ["BB"]},
    "avlfl8d0": {"cls": 120, "sub": 0, "mask": ["BB"]},
    "avlfl9d0": {"cls": 120, "sub": 0, "mask": ["B"]},
    "avlflk10": {"cls": 121, "sub": 0, "mask": ["BBBBB", "BBBBB"]},
    "avlflk20": {"cls": 121, "sub": 0, "mask": ["BBB"]},
    "avlflk30": {"cls": 121, "sub": 0, "mask": ["BB"]},
    "avlglly0": {"cls": 117, "sub": 0, "mask": ["VVBB", "BBBB", "BBVV"]},
    "avlhlds0": {"cls": 124, "sub": 0, "mask": ["B"]},
    "avlhlsn0": {"cls": 124, "sub": 0, "mask": ["B"]},
    "avlhold0": {"cls": 124, "sub": 0, "mask": ["B"]},
    "avlholg0": {"cls": 124, "sub": 0, "mask": ["B"]},
    "avlholl0": {"cls": 124, "sub": 0, "mask": ["B"]},
    "avlholr0": {"cls": 124, "sub": 0, "mask": ["B"]},
    "avlhols0": {"cls": 124, "sub": 0, "mask": ["B"]},
    "avlholx0": {"cls": 124, "sub": 0, "mask": ["B"]},
    "avlklp10": {"cls": 125, "sub": 0, "mask": ["B"]},
    "avlklp20": {"cls": 125, "sub": 0, "mask": ["B"]},
    "avllav10": {"cls": 128, "sub": 0, "mask": ["BB", "BB"]},
    "avllav20": {"cls": 127, "sub": 0, "mask": ["BBV", "BBB"]},
    "avllav30": {"cls": 127, "sub": 0, "mask": ["BBBBBB", "BBBBBB"]},
    "avllav40": {"cls": 127, "sub": 0, "mask": ["BBB", "BBB"]},
    "avllav50": {"cls": 127, "sub": 0, "mask": ["BBB", "BBB"]},
    "avllav60": {"cls": 127, "sub": 0, "mask": ["BB", "BB"]},
    "avllav70": {"cls": 127, "sub": 0, "mask": ["BB", "BB"]},
    "avllav80": {"cls": 127, "sub": 0, "mask": ["BB", "BB"]},
    "avllav90": {"cls": 127, "sub": 0, "mask": ["BBB", "BBB"]},
    "avllk1d0": {"cls": 126, "sub": 0, "mask": ["BBBBBBB", "BBBBBBB", "VVVVBBB"]},
    "avllk1g0": {"cls": 126, "sub": 0, "mask": ["BBBBB", "BBBBB", "VBBBV"]},
    "avllk1r": {"cls": 177, "sub": 0, "mask": ["VBBV", "BBBB"]},
    "avllk1s0": {"cls": 126, "sub": 0, "mask": ["BBBBB", "BBBBB", "BBBBV"]},
    "avllk1u0": {"cls": 126, "sub": 0, "mask": ["BBBBBBV", "BBBBBBB", "VBBBBBB"]},
    "avllk2d0": {"cls": 126, "sub": 0, "mask": ["BBBB", "BBBB"]},
    "avllk2g0": {"cls": 126, "sub": 0, "mask": ["BBBB", "BBBB"]},
    "avllk2s0": {"cls": 126, "sub": 0, "mask": ["BBB", "VBB"]},
    "avllk2u0": {"cls": 126, "sub": 0, "mask": ["BBBB", "BBBB"]},
    "avllk3d0": {"cls": 126, "sub": 0, "mask": ["BB"]},
    "avllk3g0": {"cls": 126, "sub": 0, "mask": ["BBB"]},
    "avllk3s0": {"cls": 126, "sub": 0, "mask": ["BB", "BB", "BB"]},
    "avllk3u0": {"cls": 126, "sub": 0, "mask": ["BB"]},
    "avlllk10": {"cls": 128, "sub": 0, "mask": ["BBB", "BBB"]},
    "avlllk20": {"cls": 128, "sub": 0, "mask": ["BB", "BB"]},
    "avllv100": {"cls": 127, "sub": 0, "mask": ["BBB", "BBB"]},
    "avllv110": {"cls": 127, "sub": 0, "mask": ["BB", "BB"]},
    "avllv120": {"cls": 127, "sub": 0, "mask": ["BB"]},
    "avllv130": {"cls": 127, "sub": 0, "mask": ["BB"]},
    "avllv140": {"cls": 127, "sub": 0, "mask": ["BB"]},
    "avllv150": {"cls": 127, "sub": 0, "mask": ["B"]},
    "avllv160": {"cls": 127, "sub": 0, "mask": ["B"]},
    "avllv170": {"cls": 127, "sub": 0, "mask": ["B"]},
    "avllv180": {"cls": 127, "sub": 0, "mask": ["BB", "BB"]},
    "avllv190": {"cls": 127, "sub": 0, "mask": ["BB", "BB"]},
    "avllv1u0": {"cls": 127, "sub": 0, "mask": ["BB", "BB"]},
    "avllv200": {"cls": 127, "sub": 0, "mask": ["BB", "BB"]},
    "avllv210": {"cls": 127, "sub": 0, "mask": ["B"]},
    "avllv220": {"cls": 127, "sub": 0, "mask": ["B"]},
    "avllv230": {"cls": 127, "sub": 0, "mask": ["B", "B"]},
    "avllv240": {"cls": 127, "sub": 0, "mask": ["BB"]},
    "avllv250": {"cls": 127, "sub": 0, "mask": ["B", "B"]},
    "avllv260": {"cls": 127, "sub": 0, "mask": ["BB"]},
    "avllv2u0": {"cls": 127, "sub": 0, "mask": ["B", "B"]},
    "avllv3u0": {"cls": 127, "sub": 0, "mask": ["BB"]},
    "avlman10": {"cls": 131, "sub": 0, "mask": ["B"]},
    "avlman20": {"cls": 131, "sub": 0, "mask": ["BB"]},
    "avlman30": {"cls": 131, "sub": 0, "mask": ["B"]},
    "avlman40": {"cls": 131, "sub": 0, "mask": ["BBB"]},
    "avlman50": {"cls": 131, "sub": 0, "mask": ["B"]},
    "avlmd1d0": {"cls": 133, "sub": 0, "mask": ["BB"]},
    "avlmd1g0": {"cls": 133, "sub": 0, "mask": ["BB"]},
    "avlmd1r0": {"cls": 133, "sub": 0, "mask": ["BB"]},
    "avlmd2d0": {"cls": 133, "sub": 0, "mask": ["BB"]},
    "avlmd2g0": {"cls": 133, "sub": 0, "mask": ["BB"]},
    "avlmd2r0": {"cls": 133, "sub": 0, "mask": ["B"]},
    "avlmd3r0": {"cls": 133, "sub": 0, "mask": ["BB"]},
    "avlmoss0": {"cls": 132, "sub": 0, "mask": ["B"]},
    "avlms010": {"cls": 129, "sub": 0, "mask": ["BB"]},
    "avlms020": {"cls": 129, "sub": 0, "mask": ["B"]},
    "avlms030": {"cls": 129, "sub": 0, "mask": ["B"]},
    "avlms040": {"cls": 129, "sub": 0, "mask": ["B"]},
    "avlms050": {"cls": 129, "sub": 0, "mask": ["B"]},
    "avlms060": {"cls": 129, "sub": 0, "mask": ["BB"]},
    "avlms070": {"cls": 129, "sub": 0, "mask": ["BB"]},
    "avlms080": {"cls": 129, "sub": 0, "mask": ["B"]},
    "avlms090": {"cls": 129, "sub": 0, "mask": ["B"]},
    "avlms100": {"cls": 129, "sub": 0, "mask": ["B"]},
    "avlms110": {"cls": 129, "sub": 0, "mask": ["B"]},
    "avlms120": {"cls": 129, "sub": 0, "mask": ["BB"]},
    "avlmtdr1": {"cls": 134, "sub": 0, "mask": ["VBBBB", "BBBBB", "BBBVV"]},
    "avlmtdr2": {"cls": 134, "sub": 0, "mask": ["BBBBV", "BBBBB", "VVBBB"]},
    "avlmtdr3": {"cls": 134, "sub": 0, "mask": ["BBB", "BBV"]},
    "avlmtdr4": {"cls": 134, "sub": 0, "mask": ["BBB", "VBB"]},
    "avlmtdr5": {"cls": 134, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlmtdr6": {"cls": 134, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlmtdr7": {"cls": 134, "sub": 0, "mask": ["VVBBB", "BBBVV"]},
    "avlmtdr8": {"cls": 134, "sub": 0, "mask": ["BBBVV", "VVBBB"]},
    "avlmtds1": {"cls": 134, "sub": 0, "mask": ["VBBBB", "BBBBB", "BBBVV"]},
    "avlmtds2": {"cls": 134, "sub": 0, "mask": ["BBBBV", "BBBBB", "VVBBB"]},
    "avlmtds3": {"cls": 134, "sub": 0, "mask": ["BBB", "BBV"]},
    "avlmtds4": {"cls": 134, "sub": 0, "mask": ["BBB", "VBB"]},
    "avlmtds5": {"cls": 134, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlmtds6": {"cls": 134, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlmtgn0": {"cls": 134, "sub": 0, "mask": ["BBBBV", "BBBBB", "VVBBB"]},
    "avlmtgn1": {"cls": 134, "sub": 0, "mask": ["VBBBB", "BBBBB", "BBBVV"]},
    "avlmtgn2": {"cls": 134, "sub": 0, "mask": ["BBB", "VBB"]},
    "avlmtgn3": {"cls": 134, "sub": 0, "mask": ["BBB", "BBV"]},
    "avlmtgn4": {"cls": 134, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlmtgn5": {"cls": 134, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlmtgr1": {"cls": 134, "sub": 0, "mask": ["VBBBB", "BBBBB", "BBBVV"]},
    "avlmtgr2": {"cls": 134, "sub": 0, "mask": ["BBBBV", "BBBBB", "VVBBB"]},
    "avlmtgr3": {"cls": 134, "sub": 0, "mask": ["BBB", "BBV"]},
    "avlmtgr4": {"cls": 134, "sub": 0, "mask": ["BBB", "VBB"]},
    "avlmtgr5": {"cls": 134, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlmtgr6": {"cls": 134, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlmtrf1": {"cls": 134, "sub": 0, "mask": ["VBBBB", "BBBBB", "BBBVV"]},
    "avlmtrf2": {"cls": 134, "sub": 0, "mask": ["BBBBV", "BBBBB", "VVBBB"]},
    "avlmtrf3": {"cls": 134, "sub": 0, "mask": ["BBB", "BBV"]},
    "avlmtrf4": {"cls": 134, "sub": 0, "mask": ["BBB", "VBB"]},
    "avlmtrf5": {"cls": 134, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlmtrf6": {"cls": 134, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlmtsb0": {"cls": 134, "sub": 0, "mask": ["BBBBV", "BBBBB", "VVBBB"]},
    "avlmtsb1": {"cls": 134, "sub": 0, "mask": ["VBBBB", "BBBBB", "BBBVV"]},
    "avlmtsb2": {"cls": 134, "sub": 0, "mask": ["BBB", "VBB"]},
    "avlmtsb3": {"cls": 134, "sub": 0, "mask": ["BBB", "BBV"]},
    "avlmtsb4": {"cls": 134, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlmtsb5": {"cls": 134, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlmtsn1": {"cls": 134, "sub": 0, "mask": ["VBBBB", "BBBBB", "BBBVV"]},
    "avlmtsn2": {"cls": 134, "sub": 0, "mask": ["BBBBV", "BBBBB", "VVBBB"]},
    "avlmtsn3": {"cls": 134, "sub": 0, "mask": ["BBB", "BBV"]},
    "avlmtsn4": {"cls": 134, "sub": 0, "mask": ["BBB", "VBB"]},
    "avlmtsn5": {"cls": 134, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlmtsn6": {"cls": 134, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlmtsw1": {"cls": 134, "sub": 0, "mask": ["VBBBB", "BBBBB", "BBBVV"]},
    "avlmtsw2": {"cls": 134, "sub": 0, "mask": ["BBBBV", "BBBBB", "VVBBB"]},
    "avlmtsw3": {"cls": 134, "sub": 0, "mask": ["BBB", "BBV"]},
    "avlmtsw4": {"cls": 134, "sub": 0, "mask": ["BBB", "VBB"]},
    "avlmtsw5": {"cls": 134, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlmtsw6": {"cls": 134, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlmtvo1": {"cls": 134, "sub": 0, "mask": ["VBBBB", "BBBBB", "BBBVV"]},
    "avlmtvo2": {"cls": 134, "sub": 0, "mask": ["VBBBV", "BBBBB", "VVBBB"]},
    "avlmtvo3": {"cls": 134, "sub": 0, "mask": ["BBB", "BBV"]},
    "avlmtvo4": {"cls": 134, "sub": 0, "mask": ["BBB", "VBB"]},
    "avlmtvo5": {"cls": 134, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlmtvo6": {"cls": 134, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlo1sn0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avlo2sn0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avlo3sn0": {"cls": 136, "sub": 0, "mask": ["BBB"]},
    "avloc1d0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc1g0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc1r0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc1u0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc2d0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc2g0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc2r0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc2u0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc3d0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc3g0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc3r0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc3u0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc4r0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avloc4u0": {"cls": 136, "sub": 0, "mask": ["BB"]},
    "avlp1sn0": {"cls": 153, "sub": 0, "mask": ["B"]},
    "avlp2sn0": {"cls": 153, "sub": 0, "mask": ["B"]},
    "avlplm10": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlplm20": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlplm30": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlplm40": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlplm50": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlpntr0": {"cls": 137, "sub": 0, "mask": ["B"]},
    "avlpntr1": {"cls": 137, "sub": 0, "mask": ["B"]},
    "avlpntr2": {"cls": 137, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlpntr3": {"cls": 137, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlpntr4": {"cls": 137, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlpntr5": {"cls": 137, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlpntr6": {"cls": 137, "sub": 0, "mask": ["VBB", "BBB", "BBV"]},
    "avlpntr7": {"cls": 137, "sub": 0, "mask": ["BBV", "BBB", "VBB"]},
    "avlr01u0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr02r0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr02u0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr03r0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr03u0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr04r0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr04u0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr05u0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr06r0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr06u0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr07r0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr07u0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr08r0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr08u0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr09r0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr09u0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr10r0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr10u0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr11r0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr11u0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr12r0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr12u0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr13r0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr13u0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr14r0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr14u0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr15r0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr15u0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr16u0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr1sn0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr2sn0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr3sn0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr4sn0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr5sn0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr6sn0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlr7sn0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlr8sn0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrd01": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlrd02": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlrd04": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlref10": {"cls": 161, "sub": 0, "mask": ["BBV", "BBB", "VBB"]},
    "avlref20": {"cls": 161, "sub": 0, "mask": ["VBB", "BBB", "BBV"]},
    "avlref30": {"cls": 161, "sub": 0, "mask": ["BB", "BB"]},
    "avlref40": {"cls": 161, "sub": 0, "mask": ["BV", "BB"]},
    "avlref50": {"cls": 161, "sub": 0, "mask": ["BB"]},
    "avlref60": {"cls": 161, "sub": 0, "mask": ["B", "B"]},
    "avlrg01": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlrg02": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlrg03": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrg04": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrg05": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrg06": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrg07": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrg08": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrg09": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrg10": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrg11": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrk1s0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrk1w0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlrk2s0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrk2w0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrk3d0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlrk3s0": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlrk3w0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrk4s0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrk4w0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrk5d0": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlroug0": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlroug1": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlroug2": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlrr01": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlrr05": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avls01s0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avls02s0": {"cls": 150, "sub": 0, "mask": ["BBB"]},
    "avls03s0": {"cls": 150, "sub": 0, "mask": ["BBBBB"]},
    "avls04s0": {"cls": 150, "sub": 0, "mask": ["BBB"]},
    "avls05s0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avls06s0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avls07s0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avls08s0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avls09s0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avls10s0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avls11s0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avls1sn0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avls2sn0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avls3sn0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlsh1d0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlsh1g0": {"cls": 150, "sub": 0, "mask": ["BBB"]},
    "avlsh1r0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avlsh2d0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlsh2g0": {"cls": 150, "sub": 0, "mask": ["BBB"]},
    "avlsh2r0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avlsh3d0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlsh3g0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlsh3r0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avlsh4d0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlsh4g0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlsh4r0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avlsh5d0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avlsh5g0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlsh5r0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avlsh6d0": {"cls": 150, "sub": 0, "mask": ["BBB"]},
    "avlsh6g0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlsh6r0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlsh7d0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlsh7r0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avlsh8d0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avlsh8r0": {"cls": 150, "sub": 0, "mask": ["B"]},
    "avlsh9r0": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlskul0": {"cls": 151, "sub": 0, "mask": ["B"]},
    "avlsntr0": {"cls": 137, "sub": 0, "mask": ["B"]},
    "avlsntr1": {"cls": 137, "sub": 0, "mask": ["B"]},
    "avlsntr2": {"cls": 137, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlsntr3": {"cls": 137, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlsntr4": {"cls": 137, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlsntr5": {"cls": 137, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlsntr6": {"cls": 137, "sub": 0, "mask": ["BBV", "BBB", "VBB"]},
    "avlsntr7": {"cls": 137, "sub": 0, "mask": ["VBB", "BBB", "BBV"]},
    "avlspit0": {"cls": 149, "sub": 0, "mask": ["BBB"]},
    "avlsptr0": {"cls": 135, "sub": 0, "mask": ["B"]},
    "avlsptr1": {"cls": 135, "sub": 0, "mask": ["B"]},
    "avlsptr2": {"cls": 135, "sub": 0, "mask": ["B"]},
    "avlsptr3": {"cls": 135, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlsptr4": {"cls": 135, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlsptr5": {"cls": 135, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlsptr6": {"cls": 135, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlsptr7": {"cls": 135, "sub": 0, "mask": ["VBB", "BBB", "BBV"]},
    "avlsptr8": {"cls": 135, "sub": 0, "mask": ["BBV", "BBB", "VBB"]},
    "avlstg10": {"cls": 147, "sub": 0, "mask": ["BB"]},
    "avlstg20": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlstg30": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlstg40": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlstg50": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlstg60": {"cls": 147, "sub": 0, "mask": ["B"]},
    "avlstm1": {"cls": 153, "sub": 0, "mask": ["B"]},
    "avlstm2": {"cls": 153, "sub": 0, "mask": ["B"]},
    "avlstm3": {"cls": 153, "sub": 0, "mask": ["B"]},
    "avlswmp0": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlswmp1": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlswmp2": {"cls": 155, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlswmp3": {"cls": 155, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlswmp4": {"cls": 155, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlswmp5": {"cls": 155, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlswmp6": {"cls": 155, "sub": 0, "mask": ["VBB", "BBB", "BBV"]},
    "avlswmp7": {"cls": 155, "sub": 0, "mask": ["BBV", "BBB", "VBB"]},
    "avlswp10": {"cls": 150, "sub": 0, "mask": ["BBBBV", "BBBBB"]},
    "avlswp20": {"cls": 150, "sub": 0, "mask": ["BBB"]},
    "avlswp30": {"cls": 150, "sub": 0, "mask": ["BBB"]},
    "avlswp40": {"cls": 150, "sub": 0, "mask": ["BB"]},
    "avlswp50": {"cls": 126, "sub": 0, "mask": ["BB"]},
    "avlswp60": {"cls": 119, "sub": 0, "mask": ["BB"]},
    "avlswp70": {"cls": 119, "sub": 0, "mask": ["B"]},
    "avlswt00": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt01": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt02": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt03": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt04": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt05": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt06": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt07": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt08": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt09": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt10": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt11": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt12": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt13": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt14": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt15": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt16": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt17": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt18": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswt19": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlswtr0": {"cls": 199, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlswtr1": {"cls": 199, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlswtr2": {"cls": 199, "sub": 0, "mask": ["VBB", "BBV"]},
    "avlswtr3": {"cls": 199, "sub": 0, "mask": ["BBV", "VBB"]},
    "avlswtr4": {"cls": 199, "sub": 0, "mask": ["VBB", "BBB", "VBV"]},
    "avlswtr5": {"cls": 199, "sub": 0, "mask": ["VBBV", "BVBB"]},
    "avlswtr6": {"cls": 199, "sub": 0, "mask": ["B", "B"]},
    "avlswtr7": {"cls": 199, "sub": 0, "mask": ["VBV", "BBB"]},
    "avlswtr8": {"cls": 199, "sub": 0, "mask": ["BB", "BB"]},
    "avlswtr9": {"cls": 199, "sub": 0, "mask": ["BBVBB", "VBBBV"]},
    "avltr1d0": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avltr2d0": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avltr3d0": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avltro00": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltro01": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltro02": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltro03": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltro04": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltro05": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltro06": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltro07": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltro08": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltro09": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltro10": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltro11": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltro12": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltrro0": {"cls": 199, "sub": 0, "mask": ["VBB", "BBV"]},
    "avltrro1": {"cls": 199, "sub": 0, "mask": ["BBV", "VBB"]},
    "avltrro2": {"cls": 199, "sub": 0, "mask": ["VBB", "BBV"]},
    "avltrro3": {"cls": 199, "sub": 0, "mask": ["BBV", "VBB"]},
    "avltrro4": {"cls": 199, "sub": 0, "mask": ["VBB", "BBV"]},
    "avltrro5": {"cls": 199, "sub": 0, "mask": ["BBV", "VBB"]},
    "avltrro6": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avltrro7": {"cls": 199, "sub": 0, "mask": ["B"]},
    "avlvol10": {"cls": 158, "sub": 0, "mask": ["BB"]},
    "avlvol20": {"cls": 158, "sub": 0, "mask": ["BB"]},
    "avlvol30": {"cls": 158, "sub": 0, "mask": ["BBB"]},
    "avlvol40": {"cls": 158, "sub": 0, "mask": ["VBV", "BBB"]},
    "avlvol50": {"cls": 158, "sub": 0, "mask": ["BBB", "BBB"]},
    "avlwlw10": {"cls": 155, "sub": 0, "mask": ["BBB"]},
    "avlwlw20": {"cls": 155, "sub": 0, "mask": ["BB"]},
    "avlwlw30": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlxds01": {"cls": 206, "sub": 0, "mask": ["BB", "BB"]},
    "avlxds02": {"cls": 206, "sub": 0, "mask": ["BB", "BB"]},
    "avlxds03": {"cls": 206, "sub": 0, "mask": ["BB"]},
    "avlxds04": {"cls": 206, "sub": 0, "mask": ["BB", "BB"]},
    "avlxds05": {"cls": 206, "sub": 0, "mask": ["VB", "BB"]},
    "avlxds06": {"cls": 206, "sub": 0, "mask": ["BB", "BB"]},
    "avlxds07": {"cls": 206, "sub": 0, "mask": ["BBB"]},
    "avlxds08": {"cls": 206, "sub": 0, "mask": ["BBB"]},
    "avlxds09": {"cls": 206, "sub": 0, "mask": ["BBB"]},
    "avlxds10": {"cls": 206, "sub": 0, "mask": ["B"]},
    "avlxds11": {"cls": 206, "sub": 0, "mask": ["B"]},
    "avlxds12": {"cls": 206, "sub": 0, "mask": ["B"]},
    "avlxdt00": {"cls": 207, "sub": 0, "mask": ["BB"]},
    "avlxdt01": {"cls": 207, "sub": 0, "mask": ["BB"]},
    "avlxdt02": {"cls": 207, "sub": 0, "mask": ["BB"]},
    "avlxdt03": {"cls": 207, "sub": 0, "mask": ["BB"]},
    "avlxdt04": {"cls": 207, "sub": 0, "mask": ["BB"]},
    "avlxdt05": {"cls": 207, "sub": 0, "mask": ["BB"]},
    "avlxdt06": {"cls": 207, "sub": 0, "mask": ["BBB"]},
    "avlxdt07": {"cls": 207, "sub": 0, "mask": ["BBB"]},
    "avlxdt08": {"cls": 207, "sub": 0, "mask": ["BBB"]},
    "avlxdt09": {"cls": 207, "sub": 0, "mask": ["BBB"]},
    "avlxdt10": {"cls": 207, "sub": 0, "mask": ["BBB"]},
    "avlxdt11": {"cls": 207, "sub": 0, "mask": ["BBB"]},
    "avlxgr01": {"cls": 208, "sub": 0, "mask": ["BB"]},
    "avlxgr02": {"cls": 208, "sub": 0, "mask": ["BB"]},
    "avlxgr03": {"cls": 208, "sub": 0, "mask": ["BB"]},
    "avlxgr04": {"cls": 208, "sub": 0, "mask": ["BB", "BB"]},
    "avlxgr05": {"cls": 208, "sub": 0, "mask": ["BB"]},
    "avlxgr06": {"cls": 208, "sub": 0, "mask": ["BB", "BB"]},
    "avlxgr07": {"cls": 208, "sub": 0, "mask": ["BBB"]},
    "avlxgr08": {"cls": 208, "sub": 0, "mask": ["BBB"]},
    "avlxgr09": {"cls": 208, "sub": 0, "mask": ["BBB"]},
    "avlxgr10": {"cls": 208, "sub": 0, "mask": ["B"]},
    "avlxgr11": {"cls": 208, "sub": 0, "mask": ["B"]},
    "avlxgr12": {"cls": 208, "sub": 0, "mask": ["B"]},
    "avlxro01": {"cls": 209, "sub": 0, "mask": ["BB"]},
    "avlxro02": {"cls": 209, "sub": 0, "mask": ["BB", "BB"]},
    "avlxro03": {"cls": 209, "sub": 0, "mask": ["BV", "BB"]},
    "avlxro04": {"cls": 209, "sub": 0, "mask": ["BB", "BB"]},
    "avlxro05": {"cls": 209, "sub": 0, "mask": ["BB"]},
    "avlxro06": {"cls": 209, "sub": 0, "mask": ["BB", "BB"]},
    "avlxro07": {"cls": 209, "sub": 0, "mask": ["BBB"]},
    "avlxro08": {"cls": 209, "sub": 0, "mask": ["BBB"]},
    "avlxro09": {"cls": 209, "sub": 0, "mask": ["BBB"]},
    "avlxro10": {"cls": 209, "sub": 0, "mask": ["B"]},
    "avlxro11": {"cls": 209, "sub": 0, "mask": ["B"]},
    "avlxro12": {"cls": 209, "sub": 0, "mask": ["B"]},
    "avlxsu01": {"cls": 210, "sub": 0, "mask": ["BB", "BB"]},
    "avlxsu02": {"cls": 210, "sub": 0, "mask": ["BB", "BB"]},
    "avlxsu03": {"cls": 210, "sub": 0, "mask": ["BB", "BB"]},
    "avlxsu04": {"cls": 210, "sub": 0, "mask": ["BB", "BB"]},
    "avlxsu05": {"cls": 210, "sub": 0, "mask": ["BB"]},
    "avlxsu06": {"cls": 210, "sub": 0, "mask": ["BV", "BB"]},
    "avlxsu07": {"cls": 210, "sub": 0, "mask": ["BBB"]},
    "avlxsu08": {"cls": 210, "sub": 0, "mask": ["BBB"]},
    "avlxsu09": {"cls": 210, "sub": 0, "mask": ["BBB"]},
    "avlxsu10": {"cls": 210, "sub": 0, "mask": ["B"]},
    "avlxsu11": {"cls": 210, "sub": 0, "mask": ["B"]},
    "avlxsu12": {"cls": 210, "sub": 0, "mask": ["B"]},
    "avlxsw01": {"cls": 211, "sub": 0, "mask": ["BB", "BB"]},
    "avlxsw02": {"cls": 211, "sub": 0, "mask": ["BB", "BB"]},
    "avlxsw03": {"cls": 211, "sub": 0, "mask": ["BB", "BB"]},
    "avlxsw04": {"cls": 211, "sub": 0, "mask": ["BB", "BB"]},
    "avlxsw05": {"cls": 211, "sub": 0, "mask": ["BB", "BB"]},
    "avlxsw06": {"cls": 211, "sub": 0, "mask": ["BB", "BB"]},
    "avlxsw07": {"cls": 211, "sub": 0, "mask": ["BBB"]},
    "avlxsw08": {"cls": 211, "sub": 0, "mask": ["BBB"]},
    "avlxsw09": {"cls": 211, "sub": 0, "mask": ["BBB"]},
    "avlxsw10": {"cls": 211, "sub": 0, "mask": ["B"]},
    "avlxsw11": {"cls": 211, "sub": 0, "mask": ["B"]},
    "avlyuc10": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlyuc20": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avlyuc30": {"cls": 155, "sub": 0, "mask": ["B"]},
    "avmabmg": {"cls": 53, "sub": 7, "mask": ["BXB"]},
    "avmalch0": {"cls": 53, "sub": 1, "mask": ["BXB"]},
    "avmalcs0": {"cls": 53, "sub": 1, "mask": ["BXB"]},
    "avmcrdr0": {"cls": 53, "sub": 4, "mask": ["BXB"]},
    "avmcrds0": {"cls": 53, "sub": 4, "mask": ["BBB", "BXB"]},
    "avmcrgr0": {"cls": 53, "sub": 4, "mask": ["BXB"]},
    "avmcrrf0": {"cls": 53, "sub": 4, "mask": ["BXB"]},
    "avmcrsn0": {"cls": 53, "sub": 4, "mask": ["BXB"]},
    "avmcrsu0": {"cls": 53, "sub": 4, "mask": ["BXB"]},
    "avmcrsw0": {"cls": 53, "sub": 4, "mask": ["BBB", "BXB"]},
    "avmcrvo0": {"cls": 53, "sub": 4, "mask": ["BXB"]},
    "avmcrys0": {"cls": 53, "sub": 4, "mask": ["BXB"]},
    "avmgedr0": {"cls": 53, "sub": 5, "mask": ["BBB", "BXB"]},
    "avmgelv0": {"cls": 53, "sub": 5, "mask": ["BBB", "BXB"]},
    "avmgems0": {"cls": 53, "sub": 5, "mask": ["BBB", "BXB"]},
    "avmgerf0": {"cls": 53, "sub": 5, "mask": ["BBB", "BXB"]},
    "avmgesn0": {"cls": 53, "sub": 5, "mask": ["BBB", "BXB"]},
    "avmgodr0": {"cls": 53, "sub": 6, "mask": ["BXB"]},
    "avmgods0": {"cls": 53, "sub": 6, "mask": ["BBB", "BXB"]},
    "avmgogr0": {"cls": 53, "sub": 6, "mask": ["BXB"]},
    "avmgold0": {"cls": 53, "sub": 6, "mask": ["BXB"]},
    "avmgorf0": {"cls": 53, "sub": 6, "mask": ["BXB"]},
    "avmgosb0": {"cls": 53, "sub": 6, "mask": ["BXB"]},
    "avmgosn0": {"cls": 53, "sub": 6, "mask": ["BXB"]},
    "avmgosw0": {"cls": 53, "sub": 6, "mask": ["BBB", "BXB"]},
    "avmgovo0": {"cls": 53, "sub": 6, "mask": ["BXB"]},
    "avmlean0": {"cls": 39, "sub": 0, "mask": ["XB"]},
    "avmordr0": {"cls": 53, "sub": 2, "mask": ["BBB", "BXB"]},
    "avmords0": {"cls": 53, "sub": 2, "mask": ["BBB", "BXB"]},
    "avmore0": {"cls": 53, "sub": 2, "mask": ["BBB", "BXB"]},
    "avmorlv0": {"cls": 53, "sub": 2, "mask": ["BBB", "BXB"]},
    "avmorro0": {"cls": 53, "sub": 2, "mask": ["BBB", "BXB"]},
    "avmorsb0": {"cls": 53, "sub": 2, "mask": ["BBB", "BXB"]},
    "avmorsn0": {"cls": 53, "sub": 2, "mask": ["BBB", "BXB"]},
    "avmorsw0": {"cls": 53, "sub": 2, "mask": ["BBB", "BXB"]},
    "avmsawd0": {"cls": 53, "sub": 0, "mask": ["BBVV", "BXBB"]},
    "avmsawg0": {"cls": 53, "sub": 0, "mask": ["BBVV", "BXBB"]},
    "avmsawl0": {"cls": 53, "sub": 0, "mask": ["BBVV", "BXBB"]},
    "avmsawr0": {"cls": 53, "sub": 0, "mask": ["BBVV", "BXBB"]},
    "avmsulf0": {"cls": 53, "sub": 3, "mask": ["BXB"]},
    "avmswds0": {"cls": 53, "sub": 0, "mask": ["BBVV", "BXBB"]},
    "avmswsn0": {"cls": 53, "sub": 0, "mask": ["BBVV", "BXBB"]},
    "avmwmsn0": {"cls": 112, "sub": 0, "mask": ["A"]},
    "avmwndd0": {"cls": 112, "sub": 0, "mask": ["A"]},
    "avmwwhl0": {"cls": 109, "sub": 0, "mask": ["BBB", "XBB"]},
    "avmwwsn0": {"cls": 109, "sub": 0, "mask": ["XBB"]},
    "avrcgen0": {"cls": 216, "sub": 0, "mask": ["BB", "XX"]},
    "avrcgen1": {"cls": 217, "sub": 0, "mask": ["BB", "XX"]},
    "avrcgen2": {"cls": 217, "sub": 1, "mask": ["BB", "XX"]},
    "avrcgen3": {"cls": 217, "sub": 2, "mask": ["BB", "XX"]},
    "avrcgen4": {"cls": 217, "sub": 3, "mask": ["BB", "XX"]},
    "avrcgen5": {"cls": 217, "sub": 4, "mask": ["BB", "XX"]},
    "avrcgen6": {"cls": 217, "sub": 5, "mask": ["BB", "XX"]},
    "avrcgen7": {"cls": 217, "sub": 6, "mask": ["BB", "XX"]},
    "avrcgn00": {"cls": 218, "sub": 0, "mask": ["BB", "XX"]},
    "avrcgn01": {"cls": 218, "sub": 1, "mask": ["BB", "XX"]},
    "avrcgn02": {"cls": 218, "sub": 2, "mask": ["BB", "XX"]},
    "avrcgn03": {"cls": 218, "sub": 3, "mask": ["BB", "XX"]},
    "avrcgn04": {"cls": 218, "sub": 4, "mask": ["BB", "XX"]},
    "avrcgn05": {"cls": 218, "sub": 5, "mask": ["BB", "XX"]},
    "avrcgn06": {"cls": 218, "sub": 6, "mask": ["BB", "XX"]},
    "avrcgn07": {"cls": 218, "sub": 7, "mask": ["BB", "XX"]},
    "avrcgn08": {"cls": 218, "sub": 8, "mask": ["BB", "XX"]},
    "avsarna0": {"cls": 4, "sub": 0, "mask": ["BBB", "BXB"]},
    "avsaxis0": {"cls": 61, "sub": 0, "mask": ["A"]},
    "avsbuoy0": {"cls": 11, "sub": 0, "mask": ["A"]},
    "avsclvd0": {"cls": 14, "sub": 0, "mask": ["BX"]},
    "avsclvg0": {"cls": 14, "sub": 0, "mask": ["BX"]},
    "avsclvs0": {"cls": 14, "sub": 0, "mask": ["BX"]},
    "avsfntn0": {"cls": 30, "sub": 0, "mask": ["A"]},
    "avsgrdn0": {"cls": 32, "sub": 0, "mask": ["A"]},
    "avsgzbo0": {"cls": 100, "sub": 0, "mask": ["A"]},
    "avsidol0": {"cls": 38, "sub": 0, "mask": ["A"]},
    "avslibr0": {"cls": 41, "sub": 0, "mask": ["BBVV", "BXBB"]},
    "avsmarl": {"cls": 23, "sub": 0, "mask": ["XB"]},
    "avsmerc0": {"cls": 51, "sub": 0, "mask": ["BXB"]},
    "avsring0": {"cls": 28, "sub": 0, "mask": ["BX"]},
    "avsschm0": {"cls": 47, "sub": 0, "mask": ["A"]},
    "avstmpl0": {"cls": 96, "sub": 0, "mask": ["BX"]},
    "avsuniv0": {"cls": 104, "sub": 0, "mask": ["VBBB", "BBBB", "BXBB"]},
    "avsutop0": {"cls": 25, "sub": 0, "mask": ["BBBBVV", "VBBBBB", "VXBBVV"]},
    "avswar20": {"cls": 107, "sub": 0, "mask": ["VBBV", "BBBB", "VXBV"]},
    "avswtch0": {"cls": 113, "sub": 0, "mask": ["A"]},
    "avtcave": {"cls": 103, "sub": 0, "mask": ["VBV", "BXB"]},
    "avtchst0": {"cls": 101, "sub": 0, "mask": ["A"]},
    "avtcrys0": {"cls": 79, "sub": 4, "mask": ["A"]},
    "avtgems0": {"cls": 79, "sub": 5, "mask": ["A"]},
    "avtgold0": {"cls": 79, "sub": 6, "mask": ["A"]},
    "avtmerc0": {"cls": 79, "sub": 1, "mask": ["A"]},
    "avtmyst0": {"cls": 55, "sub": 0, "mask": ["BXB"]},
    "avtore0": {"cls": 79, "sub": 2, "mask": ["A"]},
    "avtrndm0": {"cls": 76, "sub": 0, "mask": ["A"]},
    "avtsulf0": {"cls": 79, "sub": 3, "mask": ["A"]},
    "avtwagn0": {"cls": 105, "sub": 0, "mask": ["A"]},
    "avtwood0": {"cls": 79, "sub": 0, "mask": ["A"]},
    "avwangl": {"cls": 54, "sub": 12, "mask": ["A"]},
    "avwarch": {"cls": 54, "sub": 13, "mask": ["A"]},
    "avwazure": {"cls": 54, "sub": 132, "mask": ["A"]},
    "avwbasl": {"cls": 54, "sub": 106, "mask": ["A"]},
    "avwbehl0": {"cls": 54, "sub": 74, "mask": ["A"]},
    "avwbehx0": {"cls": 54, "sub": 75, "mask": ["A"]},
    "avwbhmt0": {"cls": 54, "sub": 96, "mask": ["A"]},
    "avwbhmx0": {"cls": 54, "sub": 97, "mask": ["A"]},
    "avwbkni0": {"cls": 54, "sub": 66, "mask": ["A"]},
    "avwbknx0": {"cls": 54, "sub": 67, "mask": ["A"]},
    "avwboar": {"cls": 54, "sub": 140, "mask": ["A"]},
    "avwbone0": {"cls": 54, "sub": 68, "mask": ["A"]},
    "avwbonx0": {"cls": 54, "sub": 69, "mask": ["A"]},
    "avwcdrg": {"cls": 54, "sub": 133, "mask": ["A"]},
    "avwcent0": {"cls": 54, "sub": 14, "mask": ["A"]},
    "avwcenx0": {"cls": 54, "sub": 15, "mask": ["A"]},
    "avwcvlr0": {"cls": 54, "sub": 10, "mask": ["A"]},
    "avwcvlx0": {"cls": 54, "sub": 11, "mask": ["A"]},
    "avwcycl0": {"cls": 54, "sub": 94, "mask": ["A"]},
    "avwcycx0": {"cls": 54, "sub": 95, "mask": ["A"]},
    "avwddrx0": {"cls": 54, "sub": 83, "mask": ["A"]},
    "avwdemn0": {"cls": 54, "sub": 48, "mask": ["A"]},
    "avwdemx0": {"cls": 54, "sub": 49, "mask": ["A"]},
    "avwdevl0": {"cls": 54, "sub": 54, "mask": ["A"]},
    "avwdevx0": {"cls": 54, "sub": 55, "mask": ["A"]},
    "avwdfir": {"cls": 54, "sub": 105, "mask": ["A"]},
    "avwdfly": {"cls": 54, "sub": 104, "mask": ["A"]},
    "avwdrag0": {"cls": 54, "sub": 26, "mask": ["A"]},
    "avwdrax0": {"cls": 54, "sub": 27, "mask": ["A"]},
    "avwdwrf0": {"cls": 54, "sub": 16, "mask": ["A"]},
    "avwdwrx0": {"cls": 54, "sub": 17, "mask": ["A"]},
    "avwefre0": {"cls": 54, "sub": 52, "mask": ["A"]},
    "avwefrx0": {"cls": 54, "sub": 53, "mask": ["A"]},
    "avwelfw0": {"cls": 54, "sub": 18, "mask": ["A"]},
    "avwelfx0": {"cls": 54, "sub": 19, "mask": ["A"]},
    "avwelma0": {"cls": 54, "sub": 112, "mask": ["A"]},
    "avwelme0": {"cls": 54, "sub": 113, "mask": ["A"]},
    "avwelmf0": {"cls": 54, "sub": 114, "mask": ["A"]},
    "avwelmw0": {"cls": 54, "sub": 115, "mask": ["A"]},
    "avwench": {"cls": 54, "sub": 136, "mask": ["A"]},
    "avwfbird": {"cls": 54, "sub": 130, "mask": ["A"]},
    "avwfdrg": {"cls": 54, "sub": 134, "mask": ["A"]},
    "avwgarg0": {"cls": 54, "sub": 30, "mask": ["A"]},
    "avwgarx0": {"cls": 54, "sub": 31, "mask": ["A"]},
    "avwgbas": {"cls": 54, "sub": 107, "mask": ["A"]},
    "avwgeni0": {"cls": 54, "sub": 36, "mask": ["A"]},
    "avwgenx0": {"cls": 54, "sub": 37, "mask": ["A"]},
    "avwglmd0": {"cls": 54, "sub": 117, "mask": ["A"]},
    "avwglmg0": {"cls": 54, "sub": 116, "mask": ["A"]},
    "avwgnll0": {"cls": 54, "sub": 98, "mask": ["A"]},
    "avwgnlx0": {"cls": 54, "sub": 99, "mask": ["A"]},
    "avwgobl0": {"cls": 54, "sub": 84, "mask": ["A"]},
    "avwgobx0": {"cls": 54, "sub": 85, "mask": ["A"]},
    "avwgog0": {"cls": 54, "sub": 44, "mask": ["A"]},
    "avwgogx0": {"cls": 54, "sub": 45, "mask": ["A"]},
    "avwgolm0": {"cls": 54, "sub": 32, "mask": ["A"]},
    "avwgolx0": {"cls": 54, "sub": 33, "mask": ["A"]},
    "avwgorg": {"cls": 54, "sub": 102, "mask": ["A"]},
    "avwgorx0": {"cls": 54, "sub": 103, "mask": ["A"]},
    "avwgrem0": {"cls": 54, "sub": 28, "mask": ["A"]},
    "avwgrex0": {"cls": 54, "sub": 29, "mask": ["A"]},
    "avwgrif": {"cls": 54, "sub": 4, "mask": ["A"]},
    "avwgrix0": {"cls": 54, "sub": 5, "mask": ["A"]},
    "avwhalf": {"cls": 54, "sub": 138, "mask": ["A"]},
    "avwharp0": {"cls": 54, "sub": 72, "mask": ["A"]},
    "avwharx0": {"cls": 54, "sub": 73, "mask": ["A"]},
    "avwhcrs": {"cls": 54, "sub": 3, "mask": ["A"]},
    "avwhoun0": {"cls": 54, "sub": 46, "mask": ["A"]},
    "avwhoux0": {"cls": 54, "sub": 47, "mask": ["A"]},
    "avwhydr": {"cls": 54, "sub": 110, "mask": ["A"]},
    "avwhydx0": {"cls": 54, "sub": 111, "mask": ["A"]},
    "avwicee": {"cls": 54, "sub": 123, "mask": ["A"]},
    "avwimp0": {"cls": 54, "sub": 42, "mask": ["A"]},
    "avwimpx0": {"cls": 54, "sub": 43, "mask": ["A"]},
    "avwinfr": {"cls": 54, "sub": 71, "mask": ["A"]},
    "avwlcrs": {"cls": 54, "sub": 2, "mask": ["A"]},
    "avwlich0": {"cls": 54, "sub": 64, "mask": ["A"]},
    "avwlicx0": {"cls": 54, "sub": 65, "mask": ["A"]},
    "avwlizr": {"cls": 54, "sub": 100, "mask": ["A"]},
    "avwlizx0": {"cls": 54, "sub": 101, "mask": ["A"]},
    "avwmage0": {"cls": 54, "sub": 34, "mask": ["A"]},
    "avwmagel": {"cls": 54, "sub": 121, "mask": ["A"]},
    "avwmagx0": {"cls": 54, "sub": 35, "mask": ["A"]},
    "avwmant0": {"cls": 54, "sub": 80, "mask": ["A"]},
    "avwmanx0": {"cls": 54, "sub": 81, "mask": ["A"]},
    "avwmeds": {"cls": 54, "sub": 76, "mask": ["A"]},
    "avwmedx0": {"cls": 54, "sub": 77, "mask": ["A"]},
    "avwmino": {"cls": 54, "sub": 78, "mask": ["A"]},
    "avwminx0": {"cls": 54, "sub": 79, "mask": ["A"]},
    "avwmon1": {"cls": 72, "sub": 0, "mask": ["A"]},
    "avwmon2": {"cls": 73, "sub": 0, "mask": ["A"]},
    "avwmon3": {"cls": 74, "sub": 0, "mask": ["A"]},
    "avwmon4": {"cls": 75, "sub": 0, "mask": ["A"]},
    "avwmon5": {"cls": 162, "sub": 0, "mask": ["A"]},
    "avwmon6": {"cls": 163, "sub": 0, "mask": ["A"]},
    "avwmon7": {"cls": 164, "sub": 0, "mask": ["A"]},
    "avwmonk": {"cls": 54, "sub": 8, "mask": ["A"]},
    "avwmonx0": {"cls": 54, "sub": 9, "mask": ["A"]},
    "avwmrnd0": {"cls": 71, "sub": 0, "mask": ["A"]},
    "avwmumy": {"cls": 54, "sub": 141, "mask": ["A"]},
    "avwnaga0": {"cls": 54, "sub": 38, "mask": ["A"]},
    "avwnagx0": {"cls": 54, "sub": 39, "mask": ["A"]},
    "avwnomd": {"cls": 54, "sub": 142, "mask": ["A"]},
    "avwnrg": {"cls": 54, "sub": 129, "mask": ["A"]},
    "avwogre0": {"cls": 54, "sub": 90, "mask": ["A"]},
    "avwogrx0": {"cls": 54, "sub": 91, "mask": ["A"]},
    "avworc0": {"cls": 54, "sub": 88, "mask": ["A"]},
    "avworcx0": {"cls": 54, "sub": 89, "mask": ["A"]},
    "avwpeas": {"cls": 54, "sub": 139, "mask": ["A"]},
    "avwpega0": {"cls": 54, "sub": 20, "mask": ["A"]},
    "avwpegx0": {"cls": 54, "sub": 21, "mask": ["A"]},
    "avwphx": {"cls": 54, "sub": 131, "mask": ["A"]},
    "avwpike": {"cls": 54, "sub": 0, "mask": ["A"]},
    "avwpikx0": {"cls": 54, "sub": 1, "mask": ["A"]},
    "avwpitf0": {"cls": 54, "sub": 50, "mask": ["A"]},
    "avwpitx0": {"cls": 54, "sub": 51, "mask": ["A"]},
    "avwpixie": {"cls": 54, "sub": 118, "mask": ["A"]},
    "avwpsye": {"cls": 54, "sub": 120, "mask": ["A"]},
    "avwrdrg": {"cls": 54, "sub": 82, "mask": ["A"]},
    "avwroc0": {"cls": 54, "sub": 92, "mask": ["A"]},
    "avwrocx0": {"cls": 54, "sub": 93, "mask": ["A"]},
    "avwrog": {"cls": 54, "sub": 143, "mask": ["A"]},
    "avwrust": {"cls": 54, "sub": 135, "mask": ["A"]},
    "avwsharp": {"cls": 54, "sub": 137, "mask": ["A"]},
    "avwskel0": {"cls": 54, "sub": 56, "mask": ["A"]},
    "avwskex0": {"cls": 54, "sub": 57, "mask": ["A"]},
    "avwsprit": {"cls": 54, "sub": 119, "mask": ["A"]},
    "avwstone": {"cls": 54, "sub": 125, "mask": ["A"]},
    "avwstorm": {"cls": 54, "sub": 127, "mask": ["A"]},
    "avwswrd0": {"cls": 54, "sub": 6, "mask": ["A"]},
    "avwswrx0": {"cls": 54, "sub": 7, "mask": ["A"]},
    "avwtitn0": {"cls": 54, "sub": 40, "mask": ["A"]},
    "avwtitx0": {"cls": 54, "sub": 41, "mask": ["A"]},
    "avwtree0": {"cls": 54, "sub": 22, "mask": ["A"]},
    "avwtrex0": {"cls": 54, "sub": 23, "mask": ["A"]},
    "avwtrll": {"cls": 54, "sub": 144, "mask": ["A"]},
    "avwtrog0": {"cls": 54, "sub": 70, "mask": ["A"]},
    "avwunic0": {"cls": 54, "sub": 24, "mask": ["A"]},
    "avwunix0": {"cls": 54, "sub": 25, "mask": ["A"]},
    "avwvamp0": {"cls": 54, "sub": 62, "mask": ["A"]},
    "avwvamx0": {"cls": 54, "sub": 63, "mask": ["A"]},
    "avwwigh": {"cls": 54, "sub": 60, "mask": ["A"]},
    "avwwigx0": {"cls": 54, "sub": 61, "mask": ["A"]},
    "avwwolf0": {"cls": 54, "sub": 86, "mask": ["A"]},
    "avwwolx0": {"cls": 54, "sub": 87, "mask": ["A"]},
    "avwwyvr": {"cls": 54, "sub": 108, "mask": ["A"]},
    "avwwyvx0": {"cls": 54, "sub": 109, "mask": ["A"]},
    "avwzomb0": {"cls": 54, "sub": 58, "mask": ["A"]},
    "avwzomx0": {"cls": 54, "sub": 59, "mask": ["A"]},
    "avxabnd0": {"cls": 53, "sub": 7, "mask": ["BXB"]},
    "avxaltar": {"cls": 2, "sub": 0, "mask": ["XB"]},
    "avxamds": {"cls": 220, "sub": 7, "mask": ["BBB", "BXB"]},
    "avxamgr": {"cls": 220, "sub": 7, "mask": ["BXB"]},
    "avxamlv": {"cls": 220, "sub": 7, "mask": ["VBV", "BXB"]},
    "avxamro": {"cls": 220, "sub": 7, "mask": ["BBV", "BXB"]},
    "avxamsn": {"cls": 220, "sub": 7, "mask": ["BXB"]},
    "avxamsu": {"cls": 220, "sub": 7, "mask": ["BXB"]},
    "avxamsw": {"cls": 220, "sub": 7, "mask": ["BBB", "BXB"]},
    "avxbgt00": {"cls": 212, "sub": 0, "mask": ["BXB"]},
    "avxbgt10": {"cls": 212, "sub": 1, "mask": ["BXB"]},
    "avxbgt20": {"cls": 212, "sub": 2, "mask": ["BXB"]},
    "avxbgt30": {"cls": 212, "sub": 3, "mask": ["BXB"]},
    "avxbgt40": {"cls": 212, "sub": 4, "mask": ["BXB"]},
    "avxbgt50": {"cls": 212, "sub": 5, "mask": ["BXB"]},
    "avxbgt60": {"cls": 212, "sub": 6, "mask": ["BXB"]},
    "avxbgt70": {"cls": 212, "sub": 7, "mask": ["BXB"]},
    "avxbnk10": {"cls": 16, "sub": 0, "mask": ["BX"]},
    "avxbnk20": {"cls": 16, "sub": 1, "mask": ["BXB"]},
    "avxbnk30": {"cls": 16, "sub": 2, "mask": ["XB"]},
    "avxbnk40": {"cls": 16, "sub": 3, "mask": ["XB"]},
    "avxbnk50": {"cls": 16, "sub": 4, "mask": ["BX"]},
    "avxbnk60": {"cls": 16, "sub": 5, "mask": ["BX"]},
    "avxbnk70": {"cls": 16, "sub": 6, "mask": ["A"]},
    "avxboat0": {"cls": 8, "sub": 0, "mask": ["A"]},
    "avxboat1": {"cls": 8, "sub": 1, "mask": ["A"]},
    "avxboat2": {"cls": 8, "sub": 2, "mask": ["A"]},
    "avxbor00": {"cls": 9, "sub": 0, "mask": ["A"]},
    "avxbor10": {"cls": 9, "sub": 1, "mask": ["A"]},
    "avxbor20": {"cls": 9, "sub": 2, "mask": ["A"]},
    "avxbor30": {"cls": 9, "sub": 3, "mask": ["A"]},
    "avxbor40": {"cls": 9, "sub": 4, "mask": ["A"]},
    "avxbor50": {"cls": 9, "sub": 5, "mask": ["A"]},
    "avxbor60": {"cls": 9, "sub": 6, "mask": ["A"]},
    "avxbor70": {"cls": 9, "sub": 7, "mask": ["A"]},
    "avxbor80": {"cls": 215, "sub": 0, "mask": ["A"]},
    "avxbttl0": {"cls": 59, "sub": 0, "mask": ["A"]},
    "avxccht0": {"cls": 82, "sub": 0, "mask": ["A"]},
    "avxcf0": {"cls": 222, "sub": 0, "mask": ["B"]},
    "avxcf1": {"cls": 222, "sub": 0, "mask": ["B"]},
    "avxcf2": {"cls": 222, "sub": 0, "mask": ["B"]},
    "avxcf3": {"cls": 222, "sub": 0, "mask": ["B"]},
    "avxcf4": {"cls": 222, "sub": 0, "mask": ["B"]},
    "avxcf5": {"cls": 222, "sub": 0, "mask": ["B"]},
    "avxcf6": {"cls": 222, "sub": 0, "mask": ["B"]},
    "avxcf7": {"cls": 222, "sub": 0, "mask": ["B"]},
    "avxcfds0": {"cls": 12, "sub": 0, "mask": ["A"]},
    "avxcflv0": {"cls": 12, "sub": 0, "mask": ["A"]},
    "avxcfsn0": {"cls": 12, "sub": 0, "mask": ["A"]},
    "avxcg1": {"cls": 223, "sub": 0, "mask": ["B"]},
    "avxcg2": {"cls": 223, "sub": 0, "mask": ["B"]},
    "avxcg3": {"cls": 223, "sub": 0, "mask": ["B"]},
    "avxcg4": {"cls": 223, "sub": 0, "mask": ["B"]},
    "avxcg5": {"cls": 223, "sub": 0, "mask": ["B"]},
    "avxcg6": {"cls": 223, "sub": 0, "mask": ["B"]},
    "avxcg7": {"cls": 223, "sub": 0, "mask": ["B"]},
    "avxcovr0": {"cls": 15, "sub": 0, "mask": ["BX"]},
    "avxcrsd0": {"cls": 21, "sub": 0, "mask": ["B"]},
    "avxdend0": {"cls": 97, "sub": 0, "mask": ["XB"]},
    "avxdent": {"cls": 97, "sub": 0, "mask": ["XB"]},
    "avxef0": {"cls": 224, "sub": 0, "mask": ["B"]},
    "avxef1": {"cls": 224, "sub": 0, "mask": ["B"]},
    "avxef2": {"cls": 224, "sub": 0, "mask": ["B"]},
    "avxef3": {"cls": 224, "sub": 0, "mask": ["B"]},
    "avxef4": {"cls": 224, "sub": 0, "mask": ["B"]},
    "avxef5": {"cls": 224, "sub": 0, "mask": ["B"]},
    "avxef6": {"cls": 224, "sub": 0, "mask": ["B"]},
    "avxef7": {"cls": 224, "sub": 0, "mask": ["B"]},
    "avxeyem0": {"cls": 27, "sub": 0, "mask": ["A"]},
    "avxff0": {"cls": 226, "sub": 0, "mask": ["B"]},
    "avxff1": {"cls": 226, "sub": 0, "mask": ["B"]},
    "avxff2": {"cls": 226, "sub": 0, "mask": ["B"]},
    "avxff3": {"cls": 226, "sub": 0, "mask": ["B"]},
    "avxff4": {"cls": 226, "sub": 0, "mask": ["B"]},
    "avxff5": {"cls": 226, "sub": 0, "mask": ["B"]},
    "avxff6": {"cls": 226, "sub": 0, "mask": ["B"]},
    "avxff7": {"cls": 226, "sub": 0, "mask": ["B"]},
    "avxfgld": {"cls": 213, "sub": 0, "mask": ["BXB"]},
    "avxfw0": {"cls": 225, "sub": 0, "mask": ["B"]},
    "avxfw1": {"cls": 225, "sub": 0, "mask": ["B"]},
    "avxfw2": {"cls": 225, "sub": 0, "mask": ["B"]},
    "avxfw3": {"cls": 225, "sub": 0, "mask": ["B"]},
    "avxfw4": {"cls": 225, "sub": 0, "mask": ["B"]},
    "avxfw5": {"cls": 225, "sub": 0, "mask": ["B"]},
    "avxfw6": {"cls": 225, "sub": 0, "mask": ["B"]},
    "avxfw7": {"cls": 225, "sub": 0, "mask": ["B"]},
    "avxfyth0": {"cls": 31, "sub": 0, "mask": ["BB", "XB"]},
    "avxgyds0": {"cls": 84, "sub": 0, "mask": ["BXB"]},
    "avxgyne0": {"cls": 84, "sub": 0, "mask": ["BXB"]},
    "avxgysn0": {"cls": 84, "sub": 0, "mask": ["BXB"]},
    "avxhg0": {"cls": 227, "sub": 0, "mask": ["B"]},
    "avxhg1": {"cls": 227, "sub": 0, "mask": ["B"]},
    "avxhg2": {"cls": 227, "sub": 0, "mask": ["B"]},
    "avxhg3": {"cls": 227, "sub": 0, "mask": ["B"]},
    "avxhg4": {"cls": 227, "sub": 0, "mask": ["B"]},
    "avxhg5": {"cls": 227, "sub": 0, "mask": ["B"]},
    "avxhg6": {"cls": 227, "sub": 0, "mask": ["B"]},
    "avxhg7": {"cls": 227, "sub": 0, "mask": ["B"]},
    "avxhild0": {"cls": 35, "sub": 0, "mask": ["XB"]},
    "avxhilg0": {"cls": 35, "sub": 0, "mask": ["XB"]},
    "avxhutm0": {"cls": 37, "sub": 0, "mask": ["A"]},
    "avxkey00": {"cls": 10, "sub": 0, "mask": ["XB"]},
    "avxkey10": {"cls": 10, "sub": 1, "mask": ["XB"]},
    "avxkey20": {"cls": 10, "sub": 2, "mask": ["XB"]},
    "avxkey30": {"cls": 10, "sub": 3, "mask": ["XB"]},
    "avxkey40": {"cls": 10, "sub": 4, "mask": ["XB"]},
    "avxkey50": {"cls": 10, "sub": 5, "mask": ["XB"]},
    "avxkey60": {"cls": 10, "sub": 6, "mask": ["XB"]},
    "avxkey70": {"cls": 10, "sub": 7, "mask": ["XB"]},
    "avxl1sh0": {"cls": 88, "sub": 0, "mask": ["A"]},
    "avxl2sh0": {"cls": 89, "sub": 0, "mask": ["A"]},
    "avxl3sh0": {"cls": 90, "sub": 0, "mask": ["A"]},
    "avxlp0": {"cls": 228, "sub": 0, "mask": ["B"]},
    "avxlp1": {"cls": 228, "sub": 0, "mask": ["B"]},
    "avxlp2": {"cls": 228, "sub": 0, "mask": ["B"]},
    "avxlp3": {"cls": 228, "sub": 0, "mask": ["B"]},
    "avxlp4": {"cls": 228, "sub": 0, "mask": ["B"]},
    "avxlp5": {"cls": 228, "sub": 0, "mask": ["B"]},
    "avxlp6": {"cls": 228, "sub": 0, "mask": ["B"]},
    "avxlp7": {"cls": 228, "sub": 0, "mask": ["B"]},
    "avxlths0": {"cls": 42, "sub": 0, "mask": ["A"]},
    "avxmags0": {"cls": 48, "sub": 0, "mask": ["AA"]},
    "avxmaps0": {"cls": 13, "sub": 1, "mask": ["BX"]},
    "avxmapu0": {"cls": 13, "sub": 2, "mask": ["BX"]},
    "avxmapw0": {"cls": 13, "sub": 0, "mask": ["BX"]},
    "avxmc0": {"cls": 229, "sub": 0, "mask": ["B"]},
    "avxmc1": {"cls": 229, "sub": 0, "mask": ["B"]},
    "avxmc2": {"cls": 229, "sub": 0, "mask": ["B"]},
    "avxmc3": {"cls": 229, "sub": 0, "mask": ["B"]},
    "avxmc4": {"cls": 229, "sub": 0, "mask": ["B"]},
    "avxmc5": {"cls": 229, "sub": 0, "mask": ["B"]},
    "avxmc6": {"cls": 229, "sub": 0, "mask": ["B"]},
    "avxmc7": {"cls": 229, "sub": 0, "mask": ["B"]},
    "avxmerm0": {"cls": 52, "sub": 0, "mask": ["BXB"]},
    "avxmktb0": {"cls": 7, "sub": 0, "mask": ["XB"]},
    "avxmn1b0": {"cls": 43, "sub": 0, "mask": ["A"]},
    "avxmn1r0": {"cls": 43, "sub": 1, "mask": ["A"]},
    "avxmn1y0": {"cls": 43, "sub": 2, "mask": ["A"]},
    "avxmn2g0": {"cls": 45, "sub": 0, "mask": ["A"]},
    "avxmn2o0": {"cls": 45, "sub": 1, "mask": ["A"]},
    "avxmn2p0": {"cls": 45, "sub": 2, "mask": ["A"]},
    "avxmn4b0": {"cls": 45, "sub": 3, "mask": ["A"]},
    "avxmn4i0": {"cls": 43, "sub": 3, "mask": ["A"]},
    "avxmn4o0": {"cls": 44, "sub": 3, "mask": ["A"]},
    "avxmn5b0": {"cls": 45, "sub": 4, "mask": ["BX"]},
    "avxmn5i0": {"cls": 43, "sub": 4, "mask": ["BX"]},
    "avxmn5o0": {"cls": 44, "sub": 4, "mask": ["BX"]},
    "avxmn6b0": {"cls": 45, "sub": 5, "mask": ["BX"]},
    "avxmn6i0": {"cls": 43, "sub": 5, "mask": ["BX"]},
    "avxmn6o0": {"cls": 44, "sub": 5, "mask": ["BX"]},
    "avxmn7b0": {"cls": 45, "sub": 6, "mask": ["BX"]},
    "avxmn7i0": {"cls": 43, "sub": 6, "mask": ["BX"]},
    "avxmn7o0": {"cls": 44, "sub": 6, "mask": ["BX"]},
    "avxmn8b0": {"cls": 45, "sub": 7, "mask": ["BX"]},
    "avxmn8i0": {"cls": 43, "sub": 7, "mask": ["BX"]},
    "avxmn8o0": {"cls": 44, "sub": 7, "mask": ["BX"]},
    "avxmp1": {"cls": 230, "sub": 0, "mask": ["B"]},
    "avxmp2": {"cls": 230, "sub": 0, "mask": ["B"]},
    "avxmp3": {"cls": 230, "sub": 0, "mask": ["B"]},
    "avxmp4": {"cls": 230, "sub": 0, "mask": ["B"]},
    "avxmp5": {"cls": 230, "sub": 0, "mask": ["B"]},
    "avxmp6": {"cls": 230, "sub": 0, "mask": ["B"]},
    "avxmp7": {"cls": 230, "sub": 0, "mask": ["B"]},
    "avxmx1b0": {"cls": 44, "sub": 0, "mask": ["A"]},
    "avxmx1r0": {"cls": 44, "sub": 1, "mask": ["A"]},
    "avxmx1y0": {"cls": 44, "sub": 2, "mask": ["A"]},
    "avxoblb": {"cls": 57, "sub": 0, "mask": ["A"]},
    "avxoblg": {"cls": 57, "sub": 0, "mask": ["A"]},
    "avxoblk": {"cls": 57, "sub": 0, "mask": ["A"]},
    "avxoblo": {"cls": 57, "sub": 0, "mask": ["A"]},
    "avxoblp": {"cls": 57, "sub": 0, "mask": ["A"]},
    "avxoblw": {"cls": 57, "sub": 0, "mask": ["A"]},
    "avxobly": {"cls": 57, "sub": 0, "mask": ["A"]},
    "avxosis0": {"cls": 56, "sub": 0, "mask": ["BB", "XX"]},
    "avxpllr0": {"cls": 60, "sub": 0, "mask": ["A"]},
    "avxplns0": {"cls": 46, "sub": 0, "mask": ["B"]},
    "avxpost0": {"cls": 99, "sub": 0, "mask": ["XB"]},
    "avxprmd0": {"cls": 63, "sub": 0, "mask": ["XB"]},
    "avxprsn0": {"cls": 62, "sub": 0, "mask": ["XB"]},
    "avxpssn": {"cls": 221, "sub": 0, "mask": ["XB"]},
    "avxpstr0": {"cls": 99, "sub": 0, "mask": ["XB"]},
    "avxreds0": {"cls": 58, "sub": 0, "mask": ["A"]},
    "avxredw": {"cls": 58, "sub": 0, "mask": ["A"]},
    "avxrk0": {"cls": 231, "sub": 0, "mask": ["B"]},
    "avxrk1": {"cls": 231, "sub": 0, "mask": ["B"]},
    "avxrk2": {"cls": 231, "sub": 0, "mask": ["B"]},
    "avxrk3": {"cls": 231, "sub": 0, "mask": ["B"]},
    "avxrk4": {"cls": 231, "sub": 0, "mask": ["B"]},
    "avxrk5": {"cls": 231, "sub": 0, "mask": ["B"]},
    "avxrk6": {"cls": 231, "sub": 0, "mask": ["B"]},
    "avxrk7": {"cls": 231, "sub": 0, "mask": ["B"]},
    "avxrlly0": {"cls": 64, "sub": 0, "mask": ["XB"]},
    "avxsanc0": {"cls": 80, "sub": 0, "mask": ["XB"]},
    "avxschl0": {"cls": 81, "sub": 0, "mask": ["A"]},
    "avxseeb0": {"cls": 83, "sub": 2, "mask": ["A"]},
    "avxseer0": {"cls": 83, "sub": 0, "mask": ["A"]},
    "avxseey0": {"cls": 83, "sub": 1, "mask": ["A"]},
    "avxshyd0": {"cls": 87, "sub": 0, "mask": ["BXB"]},
    "avxsirn0": {"cls": 92, "sub": 0, "mask": ["BXB"]},
    "avxskds0": {"cls": 22, "sub": 0, "mask": ["A"]},
    "avxsndg0": {"cls": 91, "sub": 0, "mask": ["A"]},
    "avxsnds0": {"cls": 91, "sub": 0, "mask": ["A"]},
    "avxsnlv0": {"cls": 91, "sub": 0, "mask": ["A"]},
    "avxsnsn0": {"cls": 91, "sub": 0, "mask": ["A"]},
    "avxsnsw0": {"cls": 91, "sub": 0, "mask": ["A"]},
    "avxstbl0": {"cls": 94, "sub": 0, "mask": ["XB"]},
    "avxtomb0": {"cls": 108, "sub": 0, "mask": ["XB"]},
    "avxtrek0": {"cls": 102, "sub": 0, "mask": ["A"]},
    "avxtvrn0": {"cls": 95, "sub": 0, "mask": ["BX"]},
    "avxwelg0": {"cls": 49, "sub": 0, "mask": ["A"]},
    "avxwelr0": {"cls": 49, "sub": 0, "mask": ["A"]},
    "avxwhrl0": {"cls": 111, "sub": 0, "mask": ["AAA", "AAA"]},
    "avxwlsn0": {"cls": 49, "sub": 0, "mask": ["A"]},
    "avxwtrh0": {"cls": 110, "sub": 0, "mask": ["AAAA"]},
    "avzevnt0": {"cls": 26, "sub": 0, "mask": ["A"]},
    "avzgrail": {"cls": 36, "sub": 0, "mask": ["A"]},
    "clrdelt1": {"cls": 143, "sub": 0, "mask": ["B"]},
    "clrdelt2": {"cls": 143, "sub": 0, "mask": ["B"]},
    "clrdelt3": {"cls": 143, "sub": 0, "mask": ["B"]},
    "clrdelt4": {"cls": 143, "sub": 0, "mask": ["B"]},
    "icedelt1": {"cls": 143, "sub": 0, "mask": ["B"]},
    "icedelt2": {"cls": 143, "sub": 0, "mask": ["B"]},
    "icedelt3": {"cls": 143, "sub": 0, "mask": ["B"]},
    "icedelt4": {"cls": 143, "sub": 0, "mask": ["B"]},
    "lavdelt1": {"cls": 143, "sub": 0, "mask": ["B"]},
    "lavdelt2": {"cls": 143, "sub": 0, "mask": ["B"]},
    "lavdelt3": {"cls": 143, "sub": 0, "mask": ["B"]},
    "lavdelt4": {"cls": 143, "sub": 0, "mask": ["B"]},
    "muddelt1": {"cls": 143, "sub": 0, "mask": ["B"]},
    "muddelt2": {"cls": 143, "sub": 0, "mask": ["B"]},
    "muddelt3": {"cls": 143, "sub": 0, "mask": ["B"]},
    "muddelt4": {"cls": 143, "sub": 0, "mask": ["B"]},
}
# === END GENERATED LEAF_META ===


def build_tree():
    """Return the full CLUSTER->PURPOSE->type->terrain->leaf taxonomy (the hardcoded TAXONOMY)."""
    return TAXONOMY


def iter_leaves(tree=None):
    """Yield (cluster, purpose, type, terrain, leaf_name, animation) for every leaf.

    A terrain node is a sorted list of animation DEFs (leaf name == animation) OR a
    {leaf_name: animation} dict (colour-keyed quest objects)."""
    tree = build_tree() if tree is None else tree
    for cluster, purposes in tree.items():
        for purpose, types in purposes.items():
            for typ, terrains in types.items():
                for terrain, leaves in terrains.items():
                    if isinstance(leaves, dict):
                        for name, anim in leaves.items():
                            yield cluster, purpose, typ, terrain, name, anim
                    else:
                        for anim in leaves:
                            yield cluster, purpose, typ, terrain, anim, anim


# ---------------------------------------------------------------------------
# Placement / category accessors — the ontology as the SINGLE SOURCE OF TRUTH for object
# identity, footprint mask, terrain coupling and decoration category. The whole generation
# pipeline (tile placement -> .vmap -> rendering) draws from these instead of the corpus.
# `type`/`subtype` in a placement identity come from `vcmi_ids` (same as the corpus path),
# so an ontology identity is a drop-in for the old objlib identity.
# ---------------------------------------------------------------------------

_VID = None
_ANIM_TERRAINS = None    # anim -> set(terrain names) it appears under
_ANIM_CATEGORY = None    # anim -> DECORATION type-level key (its category)
_VEG_CATEGORIES = None   # sorted list of DECORATION type-level keys
_DECOR_BY_TERRAIN = None  # terrain name -> sorted [anim, ...] of DECORATION leaves
_GAMEPLAY_BY_TP = None   # (terrain name, purpose) -> sorted [anim, ...] of non-DECORATION leaves


def _vid():
    global _VID
    if _VID is None:
        import sys
        sys.path.insert(0, _HERE)
        import vcmi_ids
        _VID = vcmi_ids
    return _VID


def _build_indexes():
    global _ANIM_TERRAINS, _ANIM_CATEGORY, _VEG_CATEGORIES, _DECOR_BY_TERRAIN, _GAMEPLAY_BY_TP
    if _ANIM_TERRAINS is not None:
        return
    at, ac, dbt, gbt = {}, {}, {}, {}
    for cluster, purpose, typ, terrain, _name, anim in iter_leaves(TAXONOMY):
        at.setdefault(anim, set()).add(terrain)
        if cluster == "DECORATION":
            ac[anim] = typ
            dbt.setdefault(terrain, set()).add(anim)
        else:
            gbt.setdefault((terrain, purpose), set()).add(anim)   # gameplay leaves by purpose
    _ANIM_TERRAINS = at
    _ANIM_CATEGORY = ac
    _VEG_CATEGORIES = sorted(set(ac.values()))
    _DECOR_BY_TERRAIN = {t: sorted(a) for t, a in dbt.items()}
    _GAMEPLAY_BY_TP = {k: sorted(a) for k, a in gbt.items()}


def _terrain_name(terrain):
    return terrain if isinstance(terrain, str) else TERRAIN_NAMES.get(terrain)


def has_animation(animation):
    """True if the ontology carries placement metadata for this animation (case-insensitive)."""
    return (animation or "").lower() in LEAF_META


def mask_of(animation):
    """B/A/V footprint rows for an animation (`obj_resolve.mask_cells` semantics; case-insensitive)."""
    m = LEAF_META.get((animation or "").lower())
    return list(m["mask"]) if m else ["B"]


def cls_sub_of(animation):
    m = LEAF_META.get((animation or "").lower())
    return (m["cls"], m["sub"]) if m else (None, None)


def is_blocking(animation):
    """True if the object's footprint blocks movement (its mask has a 'B' or 'X' cell)."""
    return any(ch in "BX" for row in mask_of(animation) for ch in row)


def footprint_size(animation):
    """Bounding-box area of the footprint (sum of row lengths) — matches the corpus convention."""
    return sum(len(row) for row in mask_of(animation))


def identity_of(animation):
    """Placement identity ``{type, subtype, animation, mask}`` for an animation — a drop-in for
    the corpus objlib identity, sourced entirely from the ontology + objects.txt metadata."""
    cls, sub = cls_sub_of(animation)
    r = _vid().resolve(cls, sub) if cls is not None else None
    return {"type": r[0] if r else None, "subtype": r[1] if r else None,
            "animation": animation, "mask": mask_of(animation)}


def terrains_of(animation):
    """Set of terrain-node names an animation appears under in the taxonomy (case-insensitive)."""
    _build_indexes()
    return set(_ANIM_TERRAINS.get((animation or "").lower(), ()))


def _decor_keys(name):
    """Terrain-node keys to pull DECORATION from for a terrain: the terrain itself plus the
    terrain-independent 'land'/'water' bucket (generic obstacles usable anywhere)."""
    keys = [name]
    if name == "water":
        keys.append("water")
    elif name != "rock":
        keys.append("land")
    return keys


def decor_pool(terrain, *, blocking=None, max_cells=None, exclude_types=()):
    """DECORATION placement identities native to a terrain (name or id), filtered by optional
    predicates: ``blocking`` (footprint blocks or not), ``max_cells`` (bounding-box area cap),
    ``exclude_types`` (ontology type-level names to drop, e.g. water features)."""
    _build_indexes()
    name = _terrain_name(terrain)
    exclude = set(exclude_types)
    out, seen = [], set()
    for k in _decor_keys(name):
        for anim in _DECOR_BY_TERRAIN.get(k, ()):
            if anim in seen or _ANIM_CATEGORY.get(anim) in exclude:
                continue
            if blocking is not None and is_blocking(anim) != blocking:
                continue
            if max_cells is not None and footprint_size(anim) > max_cells:
                continue
            seen.add(anim)
            out.append(identity_of(anim))
    return out


def gameplay_pool(terrain, purpose):
    """Placement identities for a gameplay PURPOSE (TOWN, MINE, DWELLING, REWARD_PICKUP, …) native to
    a terrain plus the terrain-independent 'land' bucket. The ontology enumerator used when the corpus
    grammar's idents for a purpose are thin/absent, so visitables and resources are always placeable.
    Returns identity dicts (drop-in for corpus idents); zero corpus."""
    _build_indexes()
    name = _terrain_name(terrain)
    out, seen = [], set()
    for k in _decor_keys(name):
        for anim in _GAMEPLAY_BY_TP.get((k, purpose), ()):
            if anim in seen:
                continue
            seen.add(anim)
            out.append(identity_of(anim))
    return out


def mines_by_resource(terrain):
    """``{resource: [identity]}`` for MINE objects placeable on a terrain — the resource bucket (wood,
    ore, gold, …) is the ontology-resolved subtype (``vcmi_ids`` -> :data:`MINE_RES`). Lets a town
    economy guarantee a wood + ore mine without touching the corpus."""
    out = {}
    for ident in gameplay_pool(terrain, "MINE"):
        sub = ident.get("subtype")
        res = MINE_RES.get(sub, sub if isinstance(sub, str) else str(sub))
        out.setdefault(res, []).append(ident)
    return out


def visitable_purposes():
    """Gameplay purposes that are 'visitable' destinations — the guaranteed-minimum set so a zone is
    never left with nothing to visit (a regression guard for the group-placement budget)."""
    return ("MINE", "DWELLING", "STAT_PERMANENT", "SPELL_SKILL", "BONUS_TEMP", "MANA")


def veg_categories():
    """The decoration category vocabulary = the ontology DECORATION type-level keys."""
    _build_indexes()
    return list(_VEG_CATEGORIES)


def category_of(animation):
    """Index of an animation's decoration category in :func:`veg_categories` (None if not decor;
    case-insensitive)."""
    _build_indexes()
    typ = _ANIM_CATEGORY.get((animation or "").lower())
    return _VEG_CATEGORIES.index(typ) if typ in _VEG_CATEGORIES else None


def decode_identity(category, terrain, rng=None):
    """Pick a concrete DECORATION identity of a category (index or type name) native to a terrain
    (falls back to the terrain-independent 'land' bucket). Uniform; deterministic if rng is None."""
    _build_indexes()
    if isinstance(category, str):
        typ = category
    elif category is not None and 0 <= category < len(_VEG_CATEGORIES):
        typ = _VEG_CATEGORIES[category]
    else:
        return None
    name = _terrain_name(terrain)
    cands = []
    for k in _decor_keys(name):
        cands += [a for a in _DECOR_BY_TERRAIN.get(k, ()) if _ANIM_CATEGORY.get(a) == typ]
    if not cands:
        return None
    anim = cands[0] if rng is None else rng.choice(sorted(set(cands)))
    return identity_of(anim)


def category_terrain_matrix():
    """bool[len(TERRAIN_NAMES)][len(categories)]: a category is present on a terrain (incl. the
    terrain-independent 'land'/'water' bucket) in the taxonomy."""
    _build_indexes()
    cidx = {t: i for i, t in enumerate(_VEG_CATEGORIES)}
    M = [[False] * len(_VEG_CATEGORIES) for _ in range(len(TERRAIN_NAMES))]
    for tid, name in TERRAIN_NAMES.items():
        for k in _decor_keys(name):
            for anim in _DECOR_BY_TERRAIN.get(k, ()):
                c = cidx.get(_ANIM_CATEGORY.get(anim))
                if c is not None:
                    M[tid][c] = True
    return M


# ---------------------------------------------------------------------------
# Regeneration: derive the taxonomy from the AUTHORITATIVE VCMI/H3 object table
# (objects.txt in the LOD) -- the absolute list the map editor places -- and rewrite
# the TAXONOMY literal above in place. Run: `python -m vcmi_mapgen.ontology --regen`.
# objects.txt columns: DEF, passability(48), triggers(48), allowedTerrains(9),
# nativeTerrain(9), class, subclass, group, isOverlay. The 9-bit terrain masks are
# MSB->LSB = terrain 8..0 (water..dirt); bit i means terrain (8 - i).
# ---------------------------------------------------------------------------


def _decode_mask(passability, triggers):
    """Decode the objects.txt passability(48)+triggers(48) bitfields into the B/A/V footprint
    mask rows (`obj_resolve.mask_cells` semantics: B=blocking, A=visitable anchor, V=visible
    overlay). This reproduces `h3m2vmap.build_mask` (the corpus mask source) bit-for-bit: the
    6x8 grid defaults to 'V', a cell is 'A' if its trigger bit is set else 'B' if its
    passability bit is clear (H3: clear=blocked); rows/cols that are all-'V' are trimmed. The
    one twist vs the .h3m byte order: objects.txt lists the 6 rows bottom-to-top, so reverse
    them (validated: 1240/1245 corpus DEFs match exactly; the few that differ are 1-2 tile
    creature dwellings where the editor table and the map instance legitimately disagree)."""
    def rows(bits):
        return [bits[r * 8:(r + 1) * 8] for r in range(6)]
    P, T = rows(passability), rows(triggers)
    grid = [["V"] * 8 for _ in range(6)]
    for r in range(6):
        for c in range(8):
            blocked = P[r][c] == "0"               # H3: passability bit clear == blocked
            visit = T[r][c] == "1"
            # four states from two independent bits; 'X' = blocked AND visitable (building action
            # tile, visited from an adjacent tile) — keep its blocked-ness instead of collapsing to A
            grid[r][c] = ("X" if blocked else "A") if visit else ("B" if blocked else "V")
    # only an object with a solid BODY ('B' cells) keeps a blocked visit tile ('X', visited from
    # adjacent); a bodyless single visit tile is a walk-onto pickup -> 'A' (passable). See build_mask.
    if not any(grid[r][c] == "B" for r in range(6) for c in range(8)):
        for r in range(6):
            for c in range(8):
                if grid[r][c] == "X":
                    grid[r][c] = "A"
    grid = grid[::-1]  # objects.txt rows are bottom-to-top relative to the .h3m mask
    keep_r = [r for r in range(6) if any(ch != "V" for ch in grid[r])]
    keep_c = [c for c in range(8) if any(grid[r][c] != "V" for r in range(6))]
    if not keep_r or not keep_c:
        return ["B"]
    return ["".join(grid[r][c] for c in keep_c) for r in keep_r]


def _decode_mask_grid(passability, triggers):
    """Return the FULL 6x8 visual footprint grid (rows top->bottom, cols left->right) aligned to
    the SPRITE, WITHOUT trimming -- what an editor-style overlay needs. H3 object masks are anchored
    at the BOTTOM-RIGHT and read bottom-to-top, RIGHT-to-LEFT, so the storage grid is rotated 180°
    (rows reversed AND columns reversed) to put it sprite-aligned. (Reversing rows only -- as the
    placement decoder :func:`_decode_mask` does -- leaves asymmetric footprints horizontally
    MIRRORED vs the art: e.g. a pine clump's blocked trunks, or a sawmill's visit tile, land on the
    wrong side.) '.' marks a tile outside the footprint (not drawn); a passable tile INSIDE the
    active bounding box is 'V' (overhang)."""
    def rows(bits):
        return [bits[r * 8:(r + 1) * 8] for r in range(6)]
    P, T = rows(passability), rows(triggers)
    grid = [["."] * 8 for _ in range(6)]
    for r in range(6):
        for c in range(8):
            blocked = P[r][c] == "0"
            visit = T[r][c] == "1"
            grid[r][c] = ("X" if blocked else "A") if visit else ("B" if blocked else ".")
    if not any(grid[r][c] == "B" for r in range(6) for c in range(8)):
        for r in range(6):
            for c in range(8):
                if grid[r][c] == "X":
                    grid[r][c] = "A"
    grid = [row[::-1] for row in grid[::-1]]        # 180°: rows bottom-to-top, cols right-to-left
    act = [(r, c) for r in range(6) for c in range(8) if grid[r][c] != "."]
    if act:                                         # passable tiles inside the footprint bbox -> 'V'
        r0, r1 = min(r for r, _ in act), max(r for r, _ in act)
        c0, c1 = min(c for _, c in act), max(c for _, c in act)
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                if grid[r][c] == ".":
                    grid[r][c] = "V"
    return ["".join(row) for row in grid]


_FULL_GRIDS = None


def full_mask_of(animation):
    """The full 6-row x 8-col visual footprint grid (B/X/A/V/'.') for an animation, bottom-left
    anchored -- for editor-style overlays. Empty ('.') outside the object footprint. See
    :func:`_decode_mask_grid`."""
    global _FULL_GRIDS
    if _FULL_GRIDS is None:
        _FULL_GRIDS = {}
        for anim, p1, p2 in _objects_txt_raw():
            if len(p1) == 48 and len(p2) == 48:
                _FULL_GRIDS[anim] = _decode_mask_grid(p1, p2)
    return _FULL_GRIDS.get((animation or "").lower())


def _objects_txt_raw():
    """[(animation, passability48, triggers48), ...] straight from objects.txt (no decode)."""
    import sys

    sys.path.insert(0, _HERE)
    import render_editor as RE

    raw = RE.lod().read("objects.txt")
    if raw is None:
        raise RuntimeError("objects.txt not found in the H3 LOD")
    out = []
    for line in raw.decode("latin1", "replace").splitlines()[1:]:
        p = line.split()
        if len(p) < 7 or not p[0].lower().endswith(".def"):
            continue
        anim = p[0][:-4].lower()
        if anim == "default":
            continue
        out.append((anim, p[1], p[2]))
    return out


def _objects_txt_records():
    """[(animation, allowedMask, nativeMask, class, subclass, mask), ...] from the LOD's
    objects.txt. ``mask`` is the decoded B/A/V footprint (see :func:`_decode_mask`)."""
    import sys

    sys.path.insert(0, _HERE)
    import render_editor as RE

    raw = RE.lod().read("objects.txt")
    if raw is None:
        raise RuntimeError("objects.txt not found in the H3 LOD")
    recs = []
    for line in raw.decode("latin1", "replace").splitlines()[1:]:
        p = line.split()
        if len(p) < 7 or not p[0].lower().endswith(".def"):
            continue
        anim = p[0][:-4].lower()
        if anim == "default":
            continue
        mask = _decode_mask(p[1], p[2]) if len(p[1]) == 48 and len(p[2]) == 48 else ["B"]
        recs.append((anim, p[3], p[4], int(p[5]), int(p[6]), mask))
    return recs


def _mask_terrains(mask):
    """9-bit objects.txt terrain mask -> set of land/water terrain names (bit i -> terrain 8-i)."""
    return {TERRAIN_NAMES[8 - i] for i, c in enumerate(mask) if c == "1"}


def _template_terrains(allowed_mask, native_mask, coupled):
    """Terrain node(s) for a template: the native terrain(s) for terrain-coupled objects, else a
    coarse land/water bucket (terrain-independent objects carry a placeholder native terrain)."""
    if coupled:
        native = sorted(_mask_terrains(native_mask))
        if not native:
            return ["land"]
        if len([t for t in native if t != "water"]) >= 8:  # native to (essentially) all land
            return ["land"]
        return native
    allowed = _mask_terrains(allowed_mask)
    if "water" in allowed and not any(t != "water" for t in allowed):
        return ["water"]
    return ["land"]


def _derive_leaf_meta():
    """{animation: {"cls", "sub", "mask"}} for every objects.txt template — the per-animation
    placement metadata the ontology exposes via :func:`identity_of` / :func:`mask_of`."""
    meta = {}
    for anim, _allowed, _native, cls, sub, mask in _objects_txt_records():
        meta[anim] = {"cls": cls, "sub": sub, "mask": mask}
    return meta


def _derive_taxonomy():
    """Build the CLUSTER->PURPOSE->type->terrain->leaf tree from objects.txt + the ontology."""
    tree = {}
    for anim, allowed, native, cls, sub, _mask in _objects_txt_records():
        r = resolve(cls, sub)
        typ = r["name"]
        if typ in COLOR_KEYED_NAMES:
            leaf_name = GATE_COLORS.get(sub, str(sub))
        elif typ in SUBTYPE_KEYED_NAMES:
            leaf_name = r["subtype"]                 # faction (castle, rampart, ...)
        else:
            leaf_name = anim
        for terrain in _template_terrains(allowed, native, r["terrain_coupled"]):
            node = (
                tree.setdefault(r["cluster"], {})
                .setdefault(r["purpose"], {})
                .setdefault(typ, {})
                .setdefault(terrain, {})
            )
            node[leaf_name] = anim
    # compact each terrain node: a plain sorted list when leaf names == animations, else a dict.
    for purposes in tree.values():
        for types in purposes.values():
            for terrains in types.values():
                for terr, leaves in list(terrains.items()):
                    if all(k == v for k, v in leaves.items()):
                        terrains[terr] = sorted(leaves.values())
    return tree


def _fmt(obj, ind=0):
    """Pretty-print the taxonomy as compact Python source (nested dicts indented; leaves inline)."""
    sp = "    " * ind
    if isinstance(obj, list):
        return "[" + ", ".join(json.dumps(x) for x in obj) + "]"
    if isinstance(obj, dict) and obj and all(isinstance(v, str) for v in obj.values()):
        return "{" + ", ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in obj.items()) + "}"
    if isinstance(obj, dict):
        items = [f"{sp}    {json.dumps(k)}: {_fmt(obj[k], ind + 1)}" for k in sorted(obj)]
        return "{\n" + ",\n".join(items) + f"\n{sp}}}"
    return json.dumps(obj)


_BEGIN = "# === BEGIN GENERATED TAXONOMY"
_END = "# === END GENERATED TAXONOMY ==="
_META_BEGIN = "# === BEGIN GENERATED LEAF_META ==="
_META_END = "# === END GENERATED LEAF_META ==="


def _fmt_leaf_meta(meta):
    """One compact line per animation: `"anim": {"cls": C, "sub": S, "mask": [...]},`."""
    lines = []
    for anim in sorted(meta):
        m = meta[anim]
        mask = "[" + ", ".join(json.dumps(row) for row in m["mask"]) + "]"
        lines.append(f'    {json.dumps(anim)}: {{"cls": {m["cls"]}, "sub": {m["sub"]}, '
                     f'"mask": {mask}}},')
    return "{\n" + "\n".join(lines) + "\n}"


def _rewrite_block(src, begin, end, text):
    """Replace the body between a BEGIN marker line and its END marker with ``text``."""
    head = src[: src.index("\n", src.index(begin)) + 1]
    tail = src[src.index(end):]
    return f"{head}{text}\n{tail}"


def regenerate():
    """Derive the taxonomy + per-animation placement metadata from objects.txt and rewrite
    both the TAXONOMY and LEAF_META literals in this file."""
    tree = _derive_taxonomy()
    meta = _derive_leaf_meta()
    os.makedirs(os.path.dirname(TREE_CACHE), exist_ok=True)
    with open(TREE_CACHE, "w") as fh:
        json.dump(tree, fh, indent=1, sort_keys=True)
    path = os.path.join(_HERE, "ontology.py")
    src = open(path).read()
    src = _rewrite_block(src, _BEGIN, _END, f"TAXONOMY = {_fmt(tree)}")
    src = _rewrite_block(src, _META_BEGIN, _META_END, f"LEAF_META = {_fmt_leaf_meta(meta)}")
    open(path, "w").write(src)
    return tree


if __name__ == "__main__":
    import sys

    tr = regenerate() if "--regen" in sys.argv else build_tree()
    n_leaf = sum(1 for _ in iter_leaves(tr))
    print(f"ontology taxonomy ({'regenerated' if '--regen' in sys.argv else 'hardcoded'})")
    for cluster in CLUSTERS:
        purposes = tr.get(cluster, {})
        types = sum(len(t) for t in purposes.values())
        leaves = sum(1 for x in iter_leaves(tr) if x[0] == cluster)
        print(f"  {cluster:11s} purposes={len(purposes):2d} types={types:3d} leaves={leaves}")
    print(f"  total leaves: {n_leaf}")
