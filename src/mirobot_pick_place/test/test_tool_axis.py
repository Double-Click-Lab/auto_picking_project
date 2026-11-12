"""Tool-axis geometry and preservation of legacy base-Z approaches."""

import math

import pytest

from mirobot_pick_place.sequence import approach_pose, positions


def pose(rx=0, ry=0, rz=0):
    return dict(x=200, y=0, z=100, rx=rx, ry=ry, rz=rz)


@pytest.mark.parametrize("attitude,delta", [
    ((0, 0, 0), (0, 0, 40)),
    ((0, -30, 0), (-20, 0, 20 * math.sqrt(3))),
    ((0, 30, 90), (0, 20, 20 * math.sqrt(3))),
    ((30, 0, 0), (0, -20, 20 * math.sqrt(3))),
    ((0, 0, 90), (0, 0, 40)),
])
def test_rpy_retract_distance_and_direction(attitude, delta):
    contact = pose(*attitude)
    target = approach_pose(contact, 40, tool_axis=True)
    actual = tuple(target[a] - contact[a] for a in ("x", "y", "z"))
    assert actual == pytest.approx(delta)
    assert math.sqrt(sum(value ** 2 for value in actual)) == pytest.approx(40)
    assert [target[a] for a in ("rx", "ry", "rz")] == list(attitude)
    assert contact == pose(*attitude)


def test_disabled_mode_is_vertical_even_at_tilted_attitude():
    contact = pose(ry=-30)
    assert approach_pose(contact, 40) == dict(contact, z=140)


@pytest.mark.parametrize("attitude", [(0, 90, 0), (180, 0, 0), (0, 120, 0)])
def test_horizontal_or_downward_retract_is_rejected(attitude):
    with pytest.raises(ValueError, match="upward"):
        approach_pose(pose(*attitude), 40, tool_axis=True)


def test_each_contact_uses_its_own_attitude():
    pick, place = pose(ry=-30), pose()
    result = positions(pose(), pick, place, 40, tool_axis=True)
    assert result["pick_approach"]["x"] == pytest.approx(180)
    assert result["place_approach"] == dict(place, z=140)


def test_explicit_approach_override_off_and_calculation_on():
    contact = pose(ry=-30)
    explicit = dict(contact, x=220, z=150)
    vertical = positions(pose(), contact, pose(), 40, pick_approach=explicit)
    tilted = positions(pose(), contact, pose(), 40,
                       pick_approach=explicit, tool_axis=True)
    assert vertical["pick_approach"] == explicit
    assert tilted["pick_approach"]["x"] == pytest.approx(180)
