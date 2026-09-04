import os

from vcmi_mapgen.kit.vmap import reader, writer
from vcmi_mapgen.kit.vmap.document import PlayerSlot, VmapDocument, VmapObject


def _sample_doc():
    return VmapDocument(
        name="Round Trip Map",
        width=3,
        height=2,
        two_level=False,
        terrain=[[["gr0_", "gr1_", "wt2_"], ["gr3_", "gr4_", "rc5_"]]],
        objects=[
            VmapObject(
                instance_name="mine_1", type="mine", subtype="7", l=0, x=2, y=1,
                animation="avlmn2", mask=["BAB"], visitable_from=["+++", "+-+", "+++"],
                options={"owner": "red"},
            ),
            VmapObject(instance_name="tree_1", type="tree", subtype="0", l=0, x=0, y=0,
                       animation="avltree", mask=["V"]),
        ],
        players=[
            PlayerSlot(id="red", can_play="PlayerOrAI", team=0,
                       main_town={"generateHero": True, "l": 0, "x": 5, "y": 5},
                       extra={"aiTactic": "random"}),
            PlayerSlot(id="blue", can_play="false"),
        ],
        teams=[["red", "blue"]],
        victory_icon_index=11,
        defeat_icon_index=3,
        triggered_events={"standardVictory": {"condition": ["standardWin", {}]}},
        extra={"versionMajor": 3, "versionMinor": 0},
    )


def test_write_then_read_round_trips_every_modeled_field(tmp_path):
    doc = _sample_doc()
    path = writer.write(doc, os.path.join(tmp_path, "roundtrip.vmap"))

    back = reader.read(path)

    assert back.name == doc.name
    assert (back.width, back.height, back.two_level) == (doc.width, doc.height, doc.two_level)
    assert back.terrain == doc.terrain
    assert back.objects == doc.objects
    assert {p.id: p for p in back.players} == {p.id: p for p in doc.players}
    assert back.teams == doc.teams
    assert back.victory_icon_index == doc.victory_icon_index
    assert back.defeat_icon_index == doc.defeat_icon_index
    assert back.triggered_events == doc.triggered_events
    assert back.extra["versionMajor"] == 3


def test_unmodeled_header_keys_survive_a_write_read_cycle(tmp_path):
    """A hand-authored .vmap can carry keys this model doesn't know about (rumors,
    disposedHeroes, ...) -- they must not be dropped on a read-then-write pass."""
    doc = _sample_doc()
    doc.extra["rumors"] = [{"name": "A Rumor", "text": {}}]
    path = writer.write(doc, os.path.join(tmp_path, "extra.vmap"))

    back = reader.read(path)
    assert back.extra["rumors"] == [{"name": "A Rumor", "text": {}}]

    # and it keeps surviving a second cycle
    path2 = writer.write(back, os.path.join(tmp_path, "extra2.vmap"))
    assert reader.read(path2).extra["rumors"] == [{"name": "A Rumor", "text": {}}]
