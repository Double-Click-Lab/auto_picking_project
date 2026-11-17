import os
import queue
import threading
import time

import pytest
import yaml

from picking_mirobot_adapter.adapter_node import MirobotTopicNode, load_fixed_plan
from mirobot_pick_place.sequence import build_plan


PLAN = os.path.join(os.path.dirname(__file__), "..", "config", "fixed_robot_plan.yaml")


def test_fixed_plan_matches_final_taught_plan_and_has_ten_steps():
    poses = load_fixed_plan(PLAN)
    steps = build_plan(poses)
    assert len(steps) == 10
    assert [step.name for step in steps] == [
        "HOME", "PICK_APPROACH", "PICK", "VACUUM_ON", "LIFT_FROM_PICK",
        "PLACE_APPROACH", "PLACE", "VACUUM_OFF", "LIFT_FROM_PLACE",
        "RETURN_HOME",
    ]
    assert poses["motion_feedrate_mm_min"] == 3000
    assert poses["approach_height"] == 60
    assert poses["pick"] == {
        "x": 277.653, "y": 0.0, "z": 101.511,
        "rx": 0.0, "ry": -30.005, "rz": 0.0,
    }
    assert poses["pick_approach"]["x"] == pytest.approx(247.6484656158)
    assert poses["pick_approach"]["z"] == pytest.approx(153.4699060353)
    assert poses["place_approach"]["z"] == pytest.approx(188.754)
    assert steps[3].pump is True and steps[7].pump is False


def test_nonfinite_pose_and_invalid_fixed_settings_rejected(tmp_path):
    with open(PLAN, encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    document["pick"]["x"] = float("nan")
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match="finite"):
        load_fixed_plan(str(bad))

    document["pick"]["x"] = 277.653
    document["approach_height_mm"] = 50
    bad.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly 60"):
        load_fixed_plan(str(bad))


def test_configured_workspace_must_contain_derived_approaches():
    bounds = {axis: [-1000, 1000] for axis in ("x", "y", "z", "rx", "ry", "rz")}
    bounds["z"] = [0, 220]
    with pytest.raises(ValueError, match="home.z"):
        load_fixed_plan(PLAN, bounds)


def test_simulation_worker_runs_the_fixed_sequence_without_controller():
    node = object.__new__(MirobotTopicNode)
    node.mode = "simulation"
    node.poses = load_fixed_plan(PLAN)
    node._worker_done = queue.SimpleQueue()
    node.get_logger = lambda: type("Logger", (), {"info": lambda *_: None})()
    command = {"command_id": "test", "mode": "simulation"}
    record = {"command": command, "deadline_mono": time.monotonic() + 10,
              "cancel_event": threading.Event()}
    node._run_plan_worker(record)
    finished_record, success, detail, unknown = node._worker_done.get_nowait()
    assert finished_record is record
    assert success is True and not unknown
    assert "10-step" in detail
