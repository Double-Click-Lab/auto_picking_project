"""Exercise Action callbacks and real SDK exclusively with fake serial."""

from pathlib import Path
from unittest.mock import patch

import pytest
import rclpy
from rclpy.action import GoalResponse
from rclpy.parameter import Parameter
from std_srvs.srv import Trigger
import yaml

from mirobot_pick_place.configuration import validate_config
from mirobot_pick_place.pick_place_action_server import PickPlaceActionServer
from mirobot_pick_place_interfaces.action import PickPlace
from mock_full_sequence_test import FakeSerial


class HardwareSerial(FakeSerial):
    def __init__(self):
        super().__init__()
        self.state = "Alarm"
        self.fail_movement = False

    def write(self, data):
        command = data.decode().strip()
        if command == "$H":
            self.writes.append(command)
            self.state = "Idle"
            self.pose = [198.67, 0, 230.72, 0, 0, 0]
            self.response += b"Info,in homing moving...\r\nok\r\n"
            return len(data)
        if command.startswith("M20") and self.fail_movement:
            self.writes.append(command)
            self.response += b"Error,E116,Invalid target\r\n"
            return len(data)
        result = super().write(data)
        if command == "?":
            self.response = self.response.replace(
                b"<Idle,", f"<{self.state},".encode()
            )
        return result


def configuration():
    path = Path(__file__).resolve().parents[1] / "config" / "poses.yaml"
    config = yaml.safe_load(path.read_text())
    config.update(
        simulation_mode=False, hardware_enable=True,
        coordinates_confirmed=True, homing_enable=True,
        startup_observation_sec=0.0, motion_timeout_sec=1.0,
        pump_settle_sec=0.0, release_settle_sec=0.0,
        # Synthetic test intervals, not hardware workspace recommendations.
        workspace_bounds={axis: [-500, 500] for axis in (
            "x", "y", "z", "rx", "ry", "rz"
        )},
    )
    return config


def goal():
    request = PickPlace.Goal()
    request.start = True
    request.pick_x, request.pick_z = 100.0, 50.0
    request.place_x, request.place_y, request.place_z = 100.0, 100.0, 50.0
    request.approach_height = 30.0
    return request


class Handle:
    def __init__(self, request, node, cancel_step=0, fail_step=0):
        self.request = request
        self.node = node
        self.cancel_step = cancel_step
        self.fail_step = fail_step
        self.is_cancel_requested = False
        self.feedback = []
        self.status = None

    def publish_feedback(self, feedback):
        self.feedback.append(feedback.current_step)
        if feedback.current_step == self.cancel_step:
            self.is_cancel_requested = True
            self.node.cancel_callback(self)
        if feedback.current_step == self.fail_step:
            self.node.controller.ser.fail_movement = True

    def succeed(self):
        self.status = "succeeded"

    def canceled(self):
        self.status = "canceled"

    def abort(self):
        self.status = "aborted"


@pytest.fixture(scope="module", autouse=True)
def ros_context():
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture
def server(tmp_path):
    config = configuration()
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    fake = HardwareSerial()
    with patch("serial.Serial", return_value=fake), patch("time.sleep"):
        node = PickPlaceActionServer(parameter_overrides=[
            Parameter("config_file", value=str(path))
        ])
        try:
            yield node, fake
        finally:
            node.destroy_node()


def initialize(node):
    result = node.initialize_callback(Trigger.Request(), Trigger.Response())
    assert result.success, result.message


def test_no_automatic_homing_and_explicit_initialization(server):
    node, fake = server
    assert not node.initialized
    assert node.goal_callback(goal()) == GoalResponse.REJECT
    assert "$H" not in fake.writes
    initialize(node)
    assert fake.writes.count("$H") == 1
    assert not any(command.startswith("M20") for command in fake.writes)
    assert node.controller.ready


def test_full_hardware_action_with_fake_serial(server):
    node, fake = server
    initialize(node)
    assert node.goal_callback(goal()) == GoalResponse.ACCEPT
    assert node.goal_callback(goal()) == GoalResponse.REJECT
    # Initialization must not interfere with an accepted goal.
    response = node.initialize_callback(Trigger.Request(), Trigger.Response())
    assert not response.success
    handle = Handle(goal(), node)
    with patch.object(node.cancel_event, "wait"):
        result = node.execute_callback(handle)
    assert result.success and handle.status == "succeeded"
    assert handle.feedback == list(range(1, 11))
    moves = [command for command in fake.writes if command.startswith("M20")]
    assert len(moves) == 8
    assert all("G01" in command and command.endswith("F100.0")
               for command in moves)
    assert fake.pump == 0
    assert not node.goal_active


def test_hardware_cancel_preserves_suction_and_blocks_next_goal(server):
    node, fake = server
    initialize(node)
    assert node.goal_callback(goal()) == GoalResponse.ACCEPT
    handle = Handle(goal(), node, cancel_step=4)
    with patch.object(node.cancel_event, "wait"):
        result = node.execute_callback(handle)
    assert not result.success and handle.status == "canceled"
    assert handle.feedback == [1, 2, 3, 4]
    assert fake.pump == 1000
    assert "M3 S0" not in fake.writes
    assert node.recovery_required
    assert node.goal_callback(goal()) == GoalResponse.REJECT
    # Rehoming with a potentially held object must not be allowed.
    homes_before = fake.writes.count("$H")
    response = node.initialize_callback(Trigger.Request(), Trigger.Response())
    assert not response.success
    assert fake.writes.count("$H") == homes_before


def test_hardware_error_preserves_pump_and_aborts(server):
    node, fake = server
    initialize(node)
    assert node.goal_callback(goal()) == GoalResponse.ACCEPT
    handle = Handle(goal(), node, fail_step=4)
    with patch.object(node.cancel_event, "wait"):
        result = node.execute_callback(handle)
    assert not result.success and handle.status == "aborted"
    assert "E116" in result.message
    assert fake.pump == 1000 and "M3 S0" not in fake.writes
    assert node.recovery_required and not node.goal_active


def test_bounds_include_approach_height(server):
    node, fake = server
    initialize(node)
    request = goal()
    request.pick_z = 490.0
    assert node.goal_callback(request) == GoalResponse.REJECT
    assert not any(command.startswith("M20") for command in fake.writes)


def test_homing_disabled_is_enforced(server):
    node, fake = server
    node.config["homing_enable"] = False
    response = node.initialize_callback(Trigger.Request(), Trigger.Response())
    assert not response.success and "$H" not in fake.writes


def test_action_tool_axis_configuration_changes_approach_geometry(server):
    node, fake = server
    request = goal()
    request.pick_ry = -30.0
    request.approach_height = 40.0
    before = list(fake.writes)
    node.config["tool_axis_approach"] = True
    poses = node.goal_poses(request)
    assert poses["pick_approach"]["x"] == pytest.approx(80)
    assert poses["pick_approach"]["z"] == pytest.approx(84.641016)
    node.config["tool_axis_approach"] = False
    poses = node.goal_poses(request)
    assert poses["pick_approach"]["x"] == 100
    assert poses["pick_approach"]["z"] == 90
    assert fake.writes == before


def test_status_service_is_cached_and_sends_nothing(server):
    node, fake = server
    before = list(fake.writes)
    result = node.status_callback(Trigger.Request(), Trigger.Response())
    assert not result.success and "cached status" in result.message
    assert fake.writes == before


def test_initialization_in_progress_rejects_goal(server):
    node, _ = server
    node.initialization_active = True
    assert node.goal_callback(goal()) == GoalResponse.REJECT
    response = node.initialize_callback(Trigger.Request(), Trigger.Response())
    assert not response.success


@pytest.mark.parametrize("change", [
    {"simulation_mode": "false"}, {"hardware_enable": False},
    {"coordinates_confirmed": False}, {"workspace_bounds": None},
    {"motion_feedrate_mm_min": 0}, {"motion_feedrate_mm_min": 3001},
    {"pump_settle_sec": -1}, {"release_settle_sec": float("nan")},
])
def test_invalid_hardware_configuration_never_opens_serial(change, tmp_path):
    config = configuration()
    config.update(change)
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(config))
    with patch("serial.Serial", side_effect=AssertionError("Unexpected IO")):
        with pytest.raises((ValueError, TypeError, KeyError)):
            validate_config(config)


def test_simulation_cleanup_failure_still_aborts_and_releases_goal(server):
    node, _ = server
    node.simulation_mode = True
    node.initialized = True
    assert node.goal_callback(goal()) == GoalResponse.ACCEPT
    handle = Handle(goal(), node)
    with patch.object(node.controller, "move_to",
                      side_effect=RuntimeError("move")):
        with patch.object(node.controller, "set_pump",
                          side_effect=RuntimeError("cleanup")):
            result = node.execute_callback(handle)
    assert not result.success and handle.status == "aborted"
    assert "cleanup" in result.message and not node.goal_active
