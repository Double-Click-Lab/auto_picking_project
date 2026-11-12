"""Manual terminal checks with fake serial; no real device is opened."""

from unittest.mock import Mock, patch

import pytest
import yaml

from mirobot_pick_place.mirobot_controller import MirobotController
from mirobot_pick_place.teach_terminal import TeachTerminal, main
from test_hardware_action import HardwareSerial


@pytest.fixture
def session(tmp_path):
    fake = HardwareSerial()
    with patch("serial.Serial", return_value=fake), patch("time.sleep"):
        controller = MirobotController(
            Mock(), simulation_mode=False, startup_observation_sec=0,
            motion_feedrate_mm_min=200.0, motion_timeout_sec=0.1,
            log_movement_targets=False,
        )
        terminal = TeachTerminal(controller, tmp_path / "poses.yaml")
        try:
            yield terminal, fake
        finally:
            terminal.shutdown()
            controller.close()


def test_requires_explicit_home_before_motion(session):
    terminal, fake = session
    with pytest.raises(RuntimeError, match="home"):
        terminal.execute("z+1")
    assert not any(c.startswith(("$H", "M20", "M3")) for c in fake.writes)


def test_home_and_single_axis_steps_preserve_posture(session):
    terminal, fake = session
    terminal.execute("home")
    terminal.execute("x+1")
    terminal.execute("y -1")
    terminal.execute("z+1")
    assert fake.writes.count("$H") == 1
    assert fake.pose == pytest.approx([199.67, -1, 231.72, 0, 0, 0])
    moves = [c for c in fake.writes if c.startswith("M20")]
    assert len(moves) == 3
    assert all("G01" in c and c.endswith("F200.0") for c in moves)
    assert not any(c.startswith(("M3", "o111")) for c in fake.writes)


@pytest.mark.parametrize("command", [
    "x 101", "step 51", "step 0.5", "z nan", "x inf",
    "goto 248.67 1 230.72",
    "goto 198.67 0 230.72", "pump on", "raw $X",
    "angle_step 6", "angle_step 0", "angle_step 0.5",
    "angle_step nan", "rx 91", "ry inf", "x+0.5", "x+101",
    "rx+0", "rx+91",
])
def test_rejects_oversized_invalid_and_raw_commands(session, command):
    terminal, fake = session
    terminal.execute("home")
    with pytest.raises(ValueError):
        terminal.execute(command)
    assert not any(c.startswith(("M20", "M3", "$X")) for c in fake.writes)


def test_fault_latches_and_prevents_repeated_movement(session):
    terminal, fake = session
    terminal.execute("home")
    fake.fail_movement = True
    with pytest.raises(RuntimeError, match="E116"):
        terminal.execute("z+1")
    assert not terminal.initialized
    count = len(fake.writes)
    with pytest.raises(RuntimeError, match="home"):
        terminal.execute("z+1")
    assert len(fake.writes) == count


def test_save_records_actual_positions_without_enabling_hardware(session):
    terminal, _ = session
    terminal.execute("home")
    terminal.execute("save home")
    terminal.execute("z-1")
    terminal.execute("save pick")
    config = yaml.safe_load(terminal.output.read_text())
    assert config["poses"]["home"]["z"] == pytest.approx(230.72)
    assert config["poses"]["pick"]["z"] == pytest.approx(229.72)
    assert config["simulation_mode"] is True
    assert config["hardware_enable"] is False


def test_existing_file_is_not_overwritten(session):
    terminal, _ = session
    terminal.output.write_text("keep existing config")
    terminal.execute("home")
    with pytest.raises(FileExistsError):
        terminal.execute("save home")
    assert terminal.output.read_text() == "keep existing config"


def test_optional_workspace_rejects_target_before_motion(session):
    terminal, fake = session
    terminal.xyz_bounds = {"x": (198, 200), "y": (-1, 1), "z": (230, 231)}
    terminal.execute("home")
    with pytest.raises(ValueError, match="session bounds"):
        terminal.execute("z-1")
    assert not any(c.startswith("M20") for c in fake.writes)


def test_quit_sends_no_commands(session):
    terminal, fake = session
    before = list(fake.writes)
    assert terminal.execute("quit") is False
    assert fake.writes == before


def test_default_simulation_never_opens_device(tmp_path):
    commands = iter(["home", "z-", "save pick", "quit"])
    with patch("serial.Serial", side_effect=AssertionError("Unexpected IO")):
        with patch("builtins.input", side_effect=lambda _: next(commands)):
            assert main(["--output", str(tmp_path / "sim.yaml")]) == 0
    assert yaml.safe_load((tmp_path / "sim.yaml").read_text())["metadata"][
        "source"
    ] == "simulation"


def test_invalid_options_refuse_device_before_open(tmp_path):
    with patch("serial.Serial", side_effect=AssertionError("Unexpected IO")):
        assert main([
            "--execute", "--xyz-bounds", "2", "1", "-1", "1", "0", "300",
            "--output", str(tmp_path / "unused.yaml"),
        ]) == 1


def test_ten_mm_shortcut_preserves_feedrate(session):
    terminal, fake = session
    terminal.execute("home")
    terminal.execute("z+10")
    assert fake.pose == pytest.approx([198.67, 0, 240.72, 0, 0, 0])
    moves = [c for c in fake.writes if c.startswith("M20")]
    assert len(moves) == 1
    assert "G01" in moves[0] and moves[0].endswith("F200.0")


def test_thirty_mm_step_preserves_orientation(session):
    terminal, fake = session
    terminal.execute("home")
    terminal.execute("z-30")
    assert fake.pose == pytest.approx([198.67, 0, 200.72, 0, 0, 0])


@pytest.mark.parametrize("axis,index", [("rx", 3), ("ry", 4), ("rz", 5)])
def test_rotation_shortcuts_use_separate_step(session, axis, index):
    terminal, fake = session
    terminal.execute("home")
    terminal.execute(f"{axis}+1")
    expected = [198.67, 0, 230.72, 0, 0, 0]
    expected[index] = 1
    assert fake.pose == pytest.approx(expected)
    terminal.execute(f"{axis}-2")
    expected[index] = -1
    assert fake.pose == pytest.approx(expected)


def test_explicit_rotation_delta_and_save(session):
    terminal, fake = session
    terminal.execute("home")
    terminal.execute("rx -2")
    terminal.execute("ry 1")
    terminal.execute("rz 5")
    terminal.execute("save pick")
    pose = yaml.safe_load(terminal.output.read_text())["poses"]["pick"]
    assert [pose[a] for a in ("rx", "ry", "rz")] == [-2, 1, 5]
    assert fake.pose[:3] == pytest.approx([198.67, 0, 230.72])
    assert not any(c.startswith("M3") for c in fake.writes)


def test_rotation_error_blocks_further_commands(session):
    terminal, fake = session
    terminal.execute("home")
    fake.fail_movement = True
    with pytest.raises(RuntimeError):
        terminal.execute("rx+1")
    before = list(fake.writes)
    with pytest.raises(RuntimeError, match="home"):
        terminal.execute("ry+1")
    assert not terminal.initialized and fake.writes == before


def taught_pair(terminal):
    terminal.execute("home")
    terminal.execute("x 10")
    terminal.execute("z -10")
    terminal.execute("save pick")
    terminal.execute("y 10")
    terminal.execute("save place")


def wait_for_sequence(terminal):
    terminal._worker.join(timeout=2)
    assert not terminal.busy


def complete_dryrun(terminal):
    terminal.execute("dryrun")
    wait_for_sequence(terminal)
    assert terminal.last_run_result == "succeeded"


def test_fifty_mm_limit_and_step_ranges(session):
    terminal, fake = session
    terminal.execute("home")
    terminal.execute("z-50")
    assert fake.pose[2] == pytest.approx(180.72)
    for command in ("x+0.9", "x+100.1", "rx+0.9", "rx+90.1"):
        with pytest.raises(ValueError):
            terminal.execute(command)
    terminal.execute("rx+90")
    assert fake.pose[3] == pytest.approx(90)


def test_run_requires_taught_pair_and_successful_dryrun(session):
    terminal, fake = session
    terminal.execute("home")
    with pytest.raises(ValueError, match="save"):
        terminal.execute("run")
    taught_pair(terminal)
    with pytest.raises(RuntimeError, match="dryrun"):
        terminal.execute("run")
    assert not any(c.startswith("M3") for c in fake.writes)


def test_default_profile_allows_run_without_plan_or_dryrun(session):
    terminal, fake = session
    taught_pair(terminal)
    terminal.execute("default")
    assert terminal.controller.motion_feedrate_mm_min == 3000
    assert terminal.tool_axis is True
    assert terminal.approach_height == 40
    assert terminal.poses["pick"] == {
        "x": 279.674, "y": 9.985, "z": 100.666,
        "rx": 0.0, "ry": -30.009, "rz": 0.0,
    }
    assert terminal.poses["place"] == {
        "x": -1.278, "y": -199.965, "z": 77.093,
        "rx": 0.0, "ry": 0.0, "rz": 0.0,
    }
    assert not any(c.startswith("M3") for c in fake.writes)

    terminal.execute("home")
    terminal.execute("default")
    terminal.execute("run")
    wait_for_sequence(terminal)

    assert terminal.last_run_result == "succeeded"
    assert any(c.startswith("M3") for c in fake.writes)


def test_changing_default_profile_afterward_requires_dryrun(session):
    terminal, fake = session
    taught_pair(terminal)
    terminal.execute("home")
    terminal.execute("default")
    terminal.execute("speed 500")
    with pytest.raises(RuntimeError, match="dryrun"):
        terminal.execute("run")
    assert not any(c.startswith("M3") for c in fake.writes)


def test_plan_never_moves_or_operates_pump(session):
    terminal, fake = session
    taught_pair(terminal)
    before = list(fake.writes)
    terminal.execute("plan")
    # plan may query status, but cannot send any motion or pump command.
    assert all(c == "?" for c in fake.writes[len(before):])


def test_save_pair_then_dryrun_then_run_same_connection(session):
    terminal, fake = session
    taught_pair(terminal)
    assert "home" in terminal.poses
    complete_dryrun(terminal)
    assert not any(c.startswith("M3") for c in fake.writes)
    before = len(fake.writes)
    with patch("serial.Serial", side_effect=AssertionError("Second port")):
        with patch.object(terminal._cancel, "wait", return_value=False):
            terminal.execute("run")
            wait_for_sequence(terminal)
    assert terminal.last_run_result == "succeeded"
    commands = fake.writes[before:]
    assert len([c for c in commands if c.startswith("M20")]) == 8
    assert commands.count("M3 S1000") == 1
    assert commands.count("M3 S0") == 1
    assert fake.writes.count("$H") == 1
    assert fake.pose == pytest.approx([198.67, 0, 230.72, 0, 0, 0])
    assert fake.pump == 0


@pytest.mark.parametrize("command", ["approach 20", "x+1", "save home"])
def test_teaching_changes_invalidate_dryrun(session, command):
    terminal, _ = session
    taught_pair(terminal)
    complete_dryrun(terminal)
    terminal.execute(command)
    with pytest.raises(RuntimeError, match="dryrun"):
        terminal.execute("run")


def test_run_checks_current_home_even_after_unchanged_dryrun(session):
    terminal, fake = session
    taught_pair(terminal)
    complete_dryrun(terminal)
    fake.pose[0] += 5  # External movement after dryrun.
    with pytest.raises(RuntimeError, match="HOME"):
        terminal.execute("run")
    assert not any(c.startswith("M3") for c in fake.writes)


def test_dryrun_error_blocks_pump_and_next_sequence(session):
    terminal, fake = session
    taught_pair(terminal)
    fake.fail_movement = True
    terminal.execute("dryrun")
    wait_for_sequence(terminal)
    assert terminal.last_run_result.startswith("failed:")
    assert not terminal.initialized
    assert not any(c.startswith("M3") for c in fake.writes)


def test_cancel_after_suction_keeps_pump_and_blocks_new_motion(session):
    terminal, fake = session
    taught_pair(terminal)
    complete_dryrun(terminal)

    def request_cancel(duration):
        assert fake.pump == 1000
        terminal.execute("cancel")
        return True

    with patch.object(terminal._cancel, "wait", side_effect=request_cancel):
        terminal.execute("run")
        wait_for_sequence(terminal)
    assert terminal.last_run_result == "canceled"
    assert fake.pump == 1000
    assert "M3 S0" not in fake.writes
    assert not terminal.initialized


def test_sequence_error_after_suction_does_not_drop_object(session):
    terminal, fake = session
    taught_pair(terminal)
    complete_dryrun(terminal)

    def fail_next_motion(duration):
        fake.fail_movement = True
        return False

    with patch.object(terminal._cancel, "wait", side_effect=fail_next_motion):
        terminal.execute("run")
        wait_for_sequence(terminal)
    assert terminal.last_run_result.startswith("failed:")
    assert fake.pump == 1000 and "M3 S0" not in fake.writes
    assert not terminal.initialized


def test_busy_blocks_manual_io_and_duplicate_run(session):
    terminal, fake = session
    taught_pair(terminal)
    terminal._busy = True
    before = list(fake.writes)
    try:
        for command in ("home", "x+", "save pick", "run", "quit"):
            with pytest.raises(RuntimeError, match="작업 중"):
                terminal.execute(command)
        terminal.execute("status")  # Cached only; no competing status query.
        assert fake.writes == before
    finally:
        terminal._busy = False


def test_load_preserves_existing_file_and_restores_taught_points(session):
    terminal, fake = session
    taught_pair(terminal)
    original = terminal.output.read_text()
    before = list(fake.writes)
    terminal.poses = {}
    terminal.execute(f"load {terminal.output}")
    assert "pick" in terminal.poses and "place" in terminal.poses
    assert terminal.output.read_text() == original
    assert fake.writes == before


def test_simulated_points_cannot_be_loaded_for_hardware(session, tmp_path):
    terminal, _ = session
    path = tmp_path / "simulated.yaml"
    path.write_text(yaml.safe_dump(dict(
        metadata=dict(source="simulation"), poses=dict(pick={})
    )))
    with pytest.raises(ValueError, match="실물"):
        terminal.execute(f"load {path}")


def test_speed_setting_does_not_actuate_and_applies_to_next_move(session):
    terminal, fake = session
    terminal.execute("home")
    before = list(fake.writes)
    terminal.execute("speed")
    terminal.execute("speed 400")
    assert fake.writes == before
    terminal.execute("z+1")
    moves = [c for c in fake.writes if c.startswith("M20")]
    assert moves[-1].endswith("F400.0")
    terminal.execute("save pick")
    assert yaml.safe_load(terminal.output.read_text())[
        "motion_feedrate_mm_min"
    ] == 400


@pytest.mark.parametrize("value", ["0", "-1", "0.5", "3001", "nan", "inf"])
def test_invalid_speed_leaves_setting_and_serial_unchanged(session, value):
    terminal, fake = session
    before = list(fake.writes)
    with pytest.raises(ValueError):
        terminal.execute(f"speed {value}")
    assert terminal.controller.motion_feedrate_mm_min == 200
    assert fake.writes == before


def test_speed_change_requires_new_dryrun(session):
    terminal, _ = session
    taught_pair(terminal)
    complete_dryrun(terminal)
    terminal.execute("speed 100")
    with pytest.raises(RuntimeError, match="dryrun"):
        terminal.execute("run")


def test_busy_sequence_refuses_speed_change(session):
    terminal, fake = session
    terminal._busy = True
    before = list(fake.writes)
    try:
        with pytest.raises(RuntimeError, match="작업 중"):
            terminal.execute("speed 400")
        assert terminal.controller.motion_feedrate_mm_min == 200
        assert fake.writes == before
    finally:
        terminal._busy = False


def test_tool_axis_toggle_has_no_motion_and_invalidates_dryrun(session):
    terminal, fake = session
    taught_pair(terminal)
    complete_dryrun(terminal)
    before = list(fake.writes)
    terminal.execute("tool_axis on")
    assert terminal.tool_axis and fake.writes == before
    with pytest.raises(RuntimeError, match="dryrun"):
        terminal.execute("run")
    terminal.execute("tool_axis off")
    assert terminal.tool_axis is False


def test_tool_axis_settings_round_trip_without_io(session):
    terminal, fake = session
    taught_pair(terminal)
    terminal.execute("tool_axis on")
    terminal.execute("approach 40")
    terminal.execute("save pick")
    config = yaml.safe_load(terminal.output.read_text())
    assert config["tool_axis_approach"] is True
    terminal.execute("tool_axis off")
    before = list(fake.writes)
    terminal.execute(f"load {terminal.output}")
    assert terminal.tool_axis and terminal.approach_height == 40
    assert fake.writes == before


def test_tool_axis_path_bounds_are_checked_before_motion(session):
    terminal, fake = session
    taught_pair(terminal)
    terminal.poses["pick"]["ry"] = -30
    terminal.xyz_bounds = {"x": (205, 210), "y": (-1, 11), "z": (0, 300)}
    terminal.poses["home"]["x"] = 208
    terminal.execute("tool_axis on")
    before = list(fake.writes)
    with pytest.raises(ValueError, match="session bounds"):
        terminal.execute("dryrun")
    assert fake.writes == before


def test_tool_axis_lift_keeps_pick_attitude(session):
    terminal, fake = session
    taught_pair(terminal)
    terminal.poses["pick"]["ry"] = -30
    terminal.execute("tool_axis on")
    terminal.execute("approach 40")
    plan, _ = terminal._make_plan()
    assert plan[4].pose == plan[1].pose
    assert plan[4].pose["ry"] == -30
    assert plan[4].pose["x"] == pytest.approx(188.67)
    complete_dryrun(terminal)
    assert not any(c.startswith("M3") for c in fake.writes)


def test_tool_axis_mode_change_is_blocked_during_execution(session):
    terminal, fake = session
    terminal._busy = True
    before = list(fake.writes)
    try:
        with pytest.raises(RuntimeError, match="작업 중"):
            terminal.execute("tool_axis on")
        assert not terminal.tool_axis and fake.writes == before
    finally:
        terminal._busy = False


def test_tool_axis_commands_are_rounded_and_traced(session):
    terminal, fake = session
    taught_pair(terminal)
    terminal.poses["pick"] = dict(
        x=289.473, y=0, z=103.671, rx=0, ry=-25.003, rz=0
    )
    terminal.execute("tool_axis on")
    terminal.execute("approach 40")
    trace = []
    terminal.controller.trace_callback = lambda event, text: trace.append(
        (event, text)
    )
    complete_dryrun(terminal)
    target_command = next(c for c in fake.writes if "X272.566" in c)
    assert "Z139.922" in target_command
    assert "272.566371" not in target_command
    # This is an output property, not an assertion about device buffer size.
    assert len(target_command) < 64
    assert ("TX", target_command) in trace
    assert ("RX", "ok") in trace
    assert terminal.controller.operation_phase == "idle"
    assert terminal.controller.last_response.startswith("<Idle,")
