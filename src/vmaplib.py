"""Read VCMI .vmap (zip of relaxed-JSON) into our normalized map representation,
with each object's PURPOSE pre-resolved from its vmap type string."""

import zipfile, re, json, os

TERR = {
    "dt": 0,
    "sa": 1,
    "gr": 2,
    "sn": 3,
    "sw": 4,
    "rg": 5,
    "sb": 6,
    "lv": 7,
    "wt": 8,
    "rc": 9,
    "hl": 10,
    "ws": 11,
}  # sb=subterranean; hl=highlands, ws=wasteland (HotA)

# vmap type string -> PURPOSE (gameplay only; everything else -> DECORATION)
VMAP_PURPOSE = {
    "town": "TOWN",
    "randomTown": "TOWN",
    "mine": "MINE",
    "abandonedMine": "MINE",
    "windmill": "MINE",
    "waterWheel": "MINE",
    "mysticalGarden": "MINE",
    "resource": "RESOURCE_PILE",
    "randomResource": "RESOURCE_PILE",
    "treasureChest": "REWARD_PICKUP",
    "campfire": "REWARD_PICKUP",
    "flotsam": "REWARD_PICKUP",
    "jetsam": "REWARD_PICKUP",
    "seaBarrel": "REWARD_PICKUP",
    "barrel": "REWARD_PICKUP",
    "wagon": "REWARD_PICKUP",
    "shipwreckSurvivor": "REWARD_PICKUP",
    "corpse": "REWARD_PICKUP",
    "skull": "REWARD_PICKUP",
    "bones": "REWARD_PICKUP",
    "leanTo": "REWARD_PICKUP",
    "seaChest": "REWARD_PICKUP",
    "warriorTomb": "REWARD_PICKUP",
    "scholar": "REWARD_PICKUP",
    "artifact": "REWARD_PICKUP",
    "randomArtifact": "REWARD_PICKUP",
    "randomArtifactMinor": "REWARD_PICKUP",
    "randomArtifactMajor": "REWARD_PICKUP",
    "randomArtifactTreasure": "REWARD_PICKUP",
    "randomArtifactRelic": "REWARD_PICKUP",
    "spellScroll": "REWARD_PICKUP",
    "pandoraBox": "REWARD_PICKUP",
    "monster": "GUARD",
    "randomMonster": "GUARD",
    "randomMonsterLevel1": "GUARD",
    "randomMonsterLevel2": "GUARD",
    "randomMonsterLevel3": "GUARD",
    "randomMonsterLevel4": "GUARD",
    "randomMonsterLevel5": "GUARD",
    "randomMonsterLevel6": "GUARD",
    "randomMonsterLevel7": "GUARD",
    "creatureBank": "BANK",
    "derelictShip": "BANK",
    "crypt": "BANK",
    "shipwreck": "BANK",
    "dragonUtopia": "BANK",
    "pyramid": "BANK",
    "monolithTwoWay": "TRANSPORT",
    "monolithOneWayEntrance": "TRANSPORT",
    "monolithOneWayExit": "TRANSPORT",
    "subterraneanGate": "TRANSPORT",
    "whirlpool": "TRANSPORT",
    "shipyard": "WATER_TRANSPORT",
    "boat": "WATER_TRANSPORT",
    "lighthouse": "WATER_TRANSPORT",
    "creatureGenerator1": "DWELLING",
    "creatureGeneratorCommon": "DWELLING",
    "creatureGeneratorSpecial": "DWELLING",
    "randomDwelling": "DWELLING",
    "randomDwellingLvl": "DWELLING",
    "randomDwellingFaction": "DWELLING",
    "refugeeCamp": "DWELLING",
    "learningStone": "STAT_PERMANENT",
    "treeOfKnowledge": "STAT_PERMANENT",
    "marlettoTower": "STAT_PERMANENT",
    "starAxis": "STAT_PERMANENT",
    "gardenOfRevelation": "STAT_PERMANENT",
    "mercenaryCamp": "STAT_PERMANENT",
    "schoolOfMagic": "STAT_PERMANENT",
    "schoolOfWar": "STAT_PERMANENT",
    "libraryOfEnlightenment": "STAT_PERMANENT",
    "arena": "STAT_PERMANENT",
    "hillFort": "STAT_PERMANENT",
    "idolOfFortune": "BONUS_TEMP",
    "fountainOfFortune": "BONUS_TEMP",
    "fountainOfYouth": "BONUS_TEMP",
    "rallyFlag": "BONUS_TEMP",
    "oasis": "BONUS_TEMP",
    "wateringHole": "BONUS_TEMP",
    "buoy": "BONUS_TEMP",
    "mermaid": "BONUS_TEMP",
    "swanPond": "BONUS_TEMP",
    "faerieRing": "BONUS_TEMP",
    "temple": "BONUS_TEMP",
    "stables": "BONUS_TEMP",
    "magicWell": "MANA",
    "shrineOfMagicLevel1": "SPELL_SKILL",
    "shrineOfMagicLevel2": "SPELL_SKILL",
    "shrineOfMagicLevel3": "SPELL_SKILL",
    "shrineOfMagicIncantation": "SPELL_SKILL",
    "shrineOfMagicGesture": "SPELL_SKILL",
    "shrineOfMagicThought": "SPELL_SKILL",
    "witchHut": "SPELL_SKILL",
    "university": "SPELL_SKILL",
    "obelisk": "INFO",
    "sign": "INFO",
    "oceanBottle": "INFO",
    "redwoodObservatory": "INFO",
    "pillarOfFire": "INFO",
    "eyeOfMagi": "INFO",
    "hutOfMagi": "INFO",
    "cartographer": "INFO",
    "denOfThieves": "INFO",
    "seerHut": "QUEST_GATE",
    "questGuard": "QUEST_GATE",
    "borderGuard": "QUEST_GATE",
    "borderGate": "QUEST_GATE",
    "keymaster": "QUEST_GATE",
    "hero": "HERO",
    "randomHero": "HERO",
    "prison": "HERO",
}


def _relaxed(t):
    t = re.sub(r"//[^\n]*", "", t)
    t = re.sub(r",(\s*[}\]])", r"\1", t)
    return json.loads(t)


def vmap_purpose(type_str):
    return VMAP_PURPOSE.get(type_str, "DECORATION")


def load(path):
    z = zipfile.ZipFile(path)
    names = z.namelist()
    surf = _relaxed(z.read("surface_terrain.json").decode())
    levels = [surf]
    if "underground_terrain.json" in names:
        levels.append(_relaxed(z.read("underground_terrain.json").decode()))

    def conv(grid):
        return [
            [{"t": TERR.get(c[:2], 2), "river": ("_ri" in c), "road": ("_ro" in c)} for c in row]
            for row in grid
        ]

    terrain = [conv(g) for g in levels]
    raw = _relaxed(z.read("objects.json").decode("utf-8", "replace"))
    objs = []
    for o in raw:
        objs.append(
            {
                "x": o["x"],
                "y": o["y"],
                "l": o.get("l", 0),
                "purpose": vmap_purpose(o.get("type", "")),
                "name": o.get("type", ""),
            }
        )
    h = len(surf)
    w = len(surf[0])
    return {
        "name": os.path.basename(path),
        "width": w,
        "height": h,
        "twoLevel": len(levels) > 1,
        "terrain": terrain,
        "objects": objs,
    }
