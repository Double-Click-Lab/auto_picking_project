"""Opt-in empty-cup pump check: explicit home, no Cartesian move."""

import argparse
import logging
import time

from .configuration import AXES
from .first_motion_check import REFERENCE
from .mirobot_controller import MirobotController


def verify_pose(status, expected):
    if status is None:
        raise RuntimeError("No robot status available.")
    for axis in AXES:
        if abs(status[axis] - expected[axis]) > 0.5:
            raise RuntimeError(f"Unexpected position {axis}: {status[axis]}")


def run_check(controller):
    """Requires an empty cup; interruption also attempts the planned OFF."""
    controller.home()
    controller.wait_until_idle(reject_busy=True)
    verify_pose(controller.last_status, REFERENCE)
    if not controller.ready:
        raise RuntimeError("Robot is not ready after homing.")
    if (controller.last_status["pump"] != 0
            or controller.last_status["valve"] != 0):
        raise RuntimeError("Pump and valve must be OFF before the test.")
    home = dict(controller.last_status)
    primary_error = None
    on_status = None
    try:
        controller.set_pump(True)
        on_status = dict(controller.last_status)
        print(f"Pump ON verified: {on_status}", flush=True)
        verify_pose(on_status, home)
        # Hold for two seconds after ON confirmation, then perform OFF once.
        time.sleep(2.0)
    except (Exception, KeyboardInterrupt) as error:
        primary_error = error
    try:
        # Uses normal readiness/ACK checks; never bypasses an Alarm or retries.
        controller.set_pump(False)
        off_status = dict(controller.last_status)
        print(f"Pump OFF verified: {off_status}", flush=True)
    except (Exception, KeyboardInterrupt) as off_error:
        raise RuntimeError(
            f"Pump OFF was not confirmed: {off_error}; "
            f"preceding error: {primary_error}. "
            "Pump may still be running; inspect the device."
        ) from off_error
    if primary_error is not None:
        raise primary_error
    verify_pose(off_status, home)
    if off_status["valve"] != 0:
        raise RuntimeError("OFF returned a nonzero valve PWM; inspect device.")
    return on_status, off_status


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument(
        "--execute", action="store_true",
        help=("Home and test ON/OFF on an EMPTY cup; "
              "operator approval required.")
    )
    options = parser.parse_args(args)
    print("Plan: one $H, verify Idle/home, pump ON, hold 2 s, pump OFF.",
          flush=True)
    print("Empty cup only. No Cartesian move, blow, or file execution.",
          flush=True)
    if not options.execute:
        print("Plan only: no serial device opened.")
        return 0
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    controller = None
    try:
        controller = MirobotController(
            logging.getLogger("pump_check"), simulation_mode=False,
            serial_port=options.port, motion_timeout_sec=60.0,
            position_tolerance_mm=0.5, orientation_tolerance_deg=0.5,
        )
        run_check(controller)
        print("Pump check completed. "
              "Physical suction/release needs observation.",
              flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"Pump check failed/interrupted: {error}", flush=True)
        return 1
    finally:
        if controller is not None:
            controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
