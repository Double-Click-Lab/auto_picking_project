"""Pump commissioning checks with fake serial and interrupted sequences."""

from unittest.mock import Mock, patch

import pytest

from mirobot_pick_place.first_motion_check import REFERENCE
from mirobot_pick_place.mirobot_controller import MirobotController
from mirobot_pick_place.pump_check import main, run_check
from test_hardware_action import HardwareSerial


def test_plan_only_never_opens_device():
    with patch("serial.Serial", side_effect=AssertionError("Unexpected IO")):
        assert main([]) == 0


def test_only_one_home_on_and_off_no_movement():
    fake = HardwareSerial()
    with patch("serial.Serial", return_value=fake), patch("time.sleep"):
        controller = MirobotController(
            Mock(), simulation_mode=False, startup_observation_sec=0,
        )
        try:
            on, off = run_check(controller)
            assert on["pump"] == 1000 and off["pump"] == 0
            assert fake.writes.count("$H") == 1
            assert fake.writes.count("M3 S1000") == 1
            assert fake.writes.count("M3 S0") == 1
            assert not any(c.startswith(("M20", "M21", "o111"))
                           for c in fake.writes)
            assert "M3 S500" not in fake.writes
        finally:
            controller.close()


def mock_controller():
    controller = Mock()
    controller.ready = True
    controller.last_status = dict(REFERENCE, pump=0, valve=0)
    return controller


def test_bad_home_blocks_pump():
    controller = mock_controller()
    controller.last_status["x"] = 0
    with pytest.raises(RuntimeError, match="Unexpected position"):
        run_check(controller)
    controller.set_pump.assert_not_called()


def test_nonempty_starting_pwm_blocks_test():
    controller = mock_controller()
    controller.last_status["pump"] = 1000
    with pytest.raises(RuntimeError, match="must be OFF"):
        run_check(controller)
    controller.set_pump.assert_not_called()


def test_interrupt_attempts_off_once_and_preserves_interrupt():
    controller = mock_controller()
    with patch("time.sleep", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            run_check(controller)
    assert [call.args for call in controller.set_pump.call_args_list] == [
        (True,), (False,),
    ]


def test_unconfirmed_on_and_off_reports_unknown_pump_state():
    controller = mock_controller()
    controller.set_pump.side_effect = [
        TimeoutError("missing ON acknowledgment"),
        RuntimeError("not ready"),
    ]
    with pytest.raises(RuntimeError, match="OFF was not confirmed") as exc:
        run_check(controller)
    assert "missing ON acknowledgment" in str(exc.value)
    assert controller.set_pump.call_count == 2
