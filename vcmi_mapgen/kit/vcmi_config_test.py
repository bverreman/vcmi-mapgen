"""Reliability test for kit.vcmi_config.resolve() — had zero coverage before this module was
split out of vcmi_ids.py; adding a minimal one since the move already touches every caller.

Requires a local VCMI install (config/objects, config/creatures, ... on disk); skipped
otherwise, same gating pattern as render_editor_test.py's H3 sprite LOD check.
"""
import pytest

from vcmi_mapgen.kit import vcmi_config as VC

pytestmark = pytest.mark.skipif(
    not VC._CLS2TYPE, reason="no local VCMI install config/ tree found"
)

# (objectClass, objectSubID) -> expected (type, subtype), captured from a real VCMI
# install's config — covers an inline-subtype object, the creature/faction/artifact
# indirection paths, and a typeless object.
KNOWN = {
    (134, 0): ("mountain", "object"),
    (101, 0): ("treasureChest", "treasureChest"),
    (53, 6): ("mine", "goldMine"),
    (79, 6): ("resource", "gold"),
    (54, 0): ("monster", "pikeman"),
    (98, 0): ("town", "castle"),
    (45, 2): ("monolithTwoWay", "monolith3"),
    (5, 0): ("artifact", "spellBook"),
}


@pytest.mark.parametrize("pair,expected", sorted(KNOWN.items()))
def test_resolve_known_pairs(pair, expected):
    assert VC.resolve(*pair) == expected


def test_resolve_unknown_class_is_none():
    assert VC.resolve(-1, 0) is None
