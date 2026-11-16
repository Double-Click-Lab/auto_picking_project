import os

import pytest
import yaml

from picking_mirobot_adapter.adapter_node import load_pose_file


EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "config", "robot_poses.example.yaml")


def test_example_pose_file_is_simulation_only():
    poses = load_pose_file(EXAMPLE, True, None, False)
    assert poses["approach_height"] > 0
    with pytest.raises(ValueError, match="real mode"):
        load_pose_file(EXAMPLE, False, None, False)


def test_nonfinite_and_nonpositive_height_rejected(tmp_path):
    data = yaml.safe_load(open(EXAMPLE, encoding="utf-8"))
    data["pick"]["x"] = float("nan")
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="finite"):
        load_pose_file(str(bad), True, None, False)
    data = yaml.safe_load(open(EXAMPLE, encoding="utf-8"))
    data["approach_height"] = 0
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="positive"):
        load_pose_file(str(bad), True, None, False)

