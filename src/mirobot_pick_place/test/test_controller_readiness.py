"""Hardware readiness checks with real SDK and exclusively fake serial IO."""

from collections import deque
from unittest.mock import Mock, patch

import pytest

from mirobot_pick_place.mirobot_controller import MirobotController


def frame(state, pump=0):
    return (
        f"<{state},Angle(ABCDXYZ):0,0,0,0,0,0,0,"
        "Cartesian coordinate(XYZ RxRyRz):198.670,0,230.720,0,0,0,"
        f"Pump PWM:{pump},Valve PWM:0,Motion_MODE:0>\r\n"
    ).encode()


class FakeSerial:
    def __init__(self, state="Idle"):
        self.is_open = False
        self.state = state
        self.lines = deque()
        self.writes = []
        self.fail_write = False
        self.fail_read = False
        self.command_reply = [b"ok\r\n"]
        self.pump = 0

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    @property
    def in_waiting(self):
        return sum(len(line) for line in self.lines)

    def write(self, data):
        if self.fail_write:
            raise OSError("disconnected")
        self.writes.append(data)
        if data.strip() == b"?":
            self.lines.append(frame(self.state, self.pump))
        else:
            self.lines.extend(self.command_reply)
        return len(data)

    def readline(self):
        if self.fail_read:
            raise OSError("disconnected")
        return self.lines.popleft() if self.lines else b""


@pytest.fixture
def hardware():
    fake = FakeSerial()
    with patch("serial.Serial", return_value=fake), patch("time.sleep"):
        controller = MirobotController(
            Mock(), simulation_mode=False, startup_observation_sec=0,
            motion_timeout_sec=0.05,
        )
        try:
            yield controller, fake
        finally:
            controller.close()


def test_idle_connect_queries_only(hardware):
    controller, fake = hardware
    assert controller.ready
    assert fake.writes == [b"?\r\n"] * 3


@pytest.mark.parametrize("boot_only", [True, False])
def test_startup_boot_and_alarm(boot_only):
    fake = FakeSerial()
    fake.lines.append(b"WLKATA Robot started successfully.\r\n")
    if not boot_only:
        fake.lines.append(frame("Alarm"))
    with patch("serial.Serial", return_value=fake), patch("time.sleep"):
        controller = MirobotController(
            Mock(), simulation_mode=False, startup_observation_sec=0.01,
        )
        try:
            assert controller.ready is boot_only
            assert all(data.strip() == b"?" for data in fake.writes)
        finally:
            controller.close()


@pytest.mark.parametrize("enabled", [True, False])
def test_alarm_blocks_pump_without_actuation(hardware, enabled):
    controller, fake = hardware
    fake.state = "Alarm"
    with pytest.raises(RuntimeError, match="Alarm"):
        controller.set_pump(enabled)
    assert not controller.ready
    assert all(data.strip() == b"?" for data in fake.writes)


def test_alarm_connect_does_not_home():
    fake = FakeSerial("Alarm")
    with patch("serial.Serial", return_value=fake):
        controller = MirobotController(
            Mock(), simulation_mode=False, startup_observation_sec=0,
        )
        try:
            assert not controller.ready
            assert fake.writes == [b"?\r\n"]
        finally:
            controller.close()


@pytest.mark.parametrize("line", [
    b"WLKATA Robot started successfully.Firmware version:20231113\r\n",
    b"Error,E112,Unsupported command\r\n",
])
def test_queued_restart_or_error_blocks_move(hardware, line):
    controller, fake = hardware
    fake.lines.append(line)
    pose = dict(x=198.67, y=0, z=230.72, rx=0, ry=0, rz=0)
    with pytest.raises(RuntimeError):
        controller.move_to("HOME", pose)
    assert not controller.ready
    assert all(data.strip() == b"?" for data in fake.writes)


@pytest.mark.parametrize("failure", ["close", "read", "write"])
def test_disconnect_invalidates_readiness(hardware, failure):
    controller, fake = hardware
    if failure == "close":
        fake.close()
    else:
        setattr(fake, f"fail_{failure}", True)
    with pytest.raises((RuntimeError, OSError)):
        controller.set_pump(True)
    assert not controller.ready


def test_missing_status_times_out(hardware):
    controller, fake = hardware
    fake.lines.clear()
    with patch.object(fake, "write", return_value=3):
        with pytest.raises(TimeoutError):
            controller.wait_until_idle(timeout=0.01)
    assert not controller.ready


def test_fragmented_status_is_reassembled(hardware):
    controller, fake = hardware
    message = frame("Idle")
    fake.lines.extend([message[:20], message[20:]])
    assert controller._read_line() == ""
    assert controller._check_response(controller._read_line()) == "Idle"


def test_close_requires_new_readiness_check(hardware):
    controller, fake = hardware
    controller.close()
    fake.open()
    with pytest.raises(RuntimeError, match="not ready"):
        controller.set_pump(True)


def test_simulation_never_constructs_serial():
    with patch("serial.Serial", side_effect=AssertionError("real IO")):
        controller = MirobotController(Mock(), simulation_step_delay_sec=0)
        controller.move_to("HOME", dict(x=0, y=0, z=0, rx=0, ry=0, rz=0))
        controller.set_pump(True)
        controller.close()


def test_command_ok_and_final_pose(hardware):
    controller, fake = hardware
    # An old OK is consumed before the new command, never used as its reply.
    fake.lines.append(b"ok\r\n")
    controller.move_to("HOME", dict(x=198.67, y=0, z=230.72,
                                    rx=0, ry=0, rz=360))
    assert controller.ready
    assert sum(data.startswith(b"M20") for data in fake.writes) == 1


@pytest.mark.parametrize("reply", [[], [frame("Idle")]])
def test_idle_or_stale_ok_cannot_replace_ack(hardware, reply):
    controller, fake = hardware
    fake.lines.append(b"ok\r\n")
    fake.command_reply = reply
    with pytest.raises(TimeoutError, match="acknowledgment"):
        controller.set_pump(False)
    assert not controller.ready
    assert controller.operation_phase == "failed"
    assert controller.last_state == "Idle"
    command_index = next(i for i, data in enumerate(fake.writes)
                         if data.startswith(b"M3"))
    assert fake.writes[command_index + 1:] == []


@pytest.mark.parametrize("reply", [
    [b"Error,E112,Unsupported command\r\n"],
    [b"error:1\r\n"],
    [b"WLKATA Robot started successfully.\r\n"],
    [frame("Alarm")],
    [b"ok\r\n", b"Error,E112,Unsupported command\r\n"],
])
def test_command_fault_is_not_success(hardware, reply):
    controller, fake = hardware
    fake.command_reply = reply
    with pytest.raises(RuntimeError):
        controller.set_pump(False)
    assert not controller.ready


def test_wrong_final_pose_rejects_ok_and_idle(hardware):
    controller, _ = hardware
    with pytest.raises(RuntimeError, match="coordinate mismatch"):
        controller.move_to(
            "TARGET", dict(x=100, y=0, z=50, rx=0, ry=0, rz=0)
        )
    assert not controller.ready


def test_wrong_final_pump_rejects_ok_and_idle(hardware):
    controller, _ = hardware
    with pytest.raises(RuntimeError, match="pump PWM mismatch"):
        controller.set_pump(True)
    assert not controller.ready


def test_pump_confirmed_by_reported_pwm(hardware):
    controller, fake = hardware
    fake.pump = 1000
    fake.command_reply = [frame("Run", 1000), b"ok\r\n"]
    controller.set_pump(True)
    assert controller.ready


def test_ack_without_final_status_times_out(hardware):
    controller, fake = hardware
    original_write = fake.write
    command_sent = False

    def write(data):
        nonlocal command_sent
        if data.startswith(b"M3"):
            command_sent = True
        elif command_sent:
            fake.writes.append(data)
            return len(data)
        return original_write(data)
    with patch.object(fake, "write", side_effect=write):
        with pytest.raises(TimeoutError, match="Idle"):
            controller.set_pump(False)
    assert not controller.ready


def test_external_motion_does_not_silently_queue_new_work(hardware):
    controller, fake = hardware
    fake.state = "Run"
    with pytest.raises(RuntimeError, match="command refused"):
        controller.set_pump(True)
    assert not controller.ready
    assert not any(data.startswith(b"M3") for data in fake.writes)


def test_explicit_home_refuses_busy_robot(hardware):
    controller, fake = hardware
    fake.state = "Run"
    with pytest.raises(RuntimeError, match="homing refused"):
        controller.home()
    assert not controller.ready
    assert b"$H\r\n" not in fake.writes


def test_outside_workspace_blocks_before_motion(hardware):
    controller, fake = hardware
    controller.workspace_bounds = {
        axis: [-1, 1] for axis in ("x", "y", "z", "rx", "ry", "rz")
    }
    with pytest.raises(ValueError, match="outside"):
        controller.move_to(
            "TARGET", dict(x=2, y=0, z=0, rx=0, ry=0, rz=0)
        )
    assert not any(data.startswith(b"M20") for data in fake.writes)
