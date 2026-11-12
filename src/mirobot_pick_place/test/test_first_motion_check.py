"""Commissioning sequence checks; no real serial devices."""

from unittest.mock import Mock, patch

import pytest

from mirobot_pick_place.first_motion_check import main, run_check, REFERENCE
from mirobot_pick_place.mirobot_controller import MirobotController
from test_hardware_action import HardwareSerial


def test_plan_only_never_opens_port():
    with patch("serial.Serial", side_effect=AssertionError("Unexpected IO")):
        assert main([]) == 0


def test_exactly_one_home_and_one_move():
    fake = HardwareSerial()
    with patch("serial.Serial", return_value=fake), patch("time.sleep"):
        controller = MirobotController(
            Mock(), simulation_mode=False, startup_observation_sec=0,
            motion_feedrate_mm_min=100.0,
        )
        try:
            target = run_check(controller)
            assert target["z"] == pytest.approx(232.720)
            assert fake.writes.count("$H") == 1
            moves = [c for c in fake.writes if c.startswith("M20")]
            assert len(moves) == 1
            assert "G01" in moves[0] and moves[0].endswith("F100.0")
            assert not any(c.startswith(("M3", "o111")) for c in fake.writes)
        finally:
            controller.close()


def test_unexpected_home_never_moves():
    controller = Mock()
    controller.ready = True
    controller.last_status = dict(REFERENCE, x=150, pump=0, valve=0)
    with pytest.raises(RuntimeError, match="Unexpected home"):
        run_check(controller)
    controller.move_to.assert_not_called()
