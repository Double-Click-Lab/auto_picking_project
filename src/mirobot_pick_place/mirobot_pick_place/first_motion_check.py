"""Opt-in commissioning check: explicit home, then one 2 mm Z move."""

import argparse
import logging

from .configuration import AXES
from .mirobot_controller import MirobotController


REFERENCE = dict(x=198.670, y=0.0, z=230.720, rx=0.0, ry=0.0, rz=0.0)


def run_check(controller):
    controller.home()
    controller.wait_until_idle(reject_busy=True)
    status = controller.last_status
    if not controller.ready or status is None:
        raise RuntimeError("No verified Idle status after homing.")
    for axis in AXES:
        if abs(status[axis] - REFERENCE[axis]) > 0.5:
            raise RuntimeError(f"Unexpected home {axis}: {status[axis]}")
    if status["pump"] != 0 or status["valve"] != 0:
        raise RuntimeError("Pump/valve must remain off for this check.")
    target = {axis: status[axis] for axis in AXES}
    target["z"] += 2.0
    controller.move_to("FIRST_Z_PLUS_2_MM", target)
    return target


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument(
        "--execute", action="store_true",
        help=("Physically home once and move Z +2 mm; "
              "requires operator approval.")
    )
    options = parser.parse_args(args)
    print("Plan: one $H, verify Idle/home, then one G01 Z+2 mm at F100.",
          flush=True)
    print("No pump operation, file execution, retry, or return movement.",
          flush=True)
    if not options.execute:
        print("Plan only: no serial device opened. --execute enables motion.")
        return 0
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    controller = None
    try:
        controller = MirobotController(
            logging.getLogger("first_motion_check"),
            simulation_mode=False, serial_port=options.port,
            motion_timeout_sec=60.0, motion_feedrate_mm_min=100.0,
            position_tolerance_mm=0.5, orientation_tolerance_deg=0.5,
        )
        target = run_check(controller)
        print(f"Verified final target: {target}", flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"Check did not complete: {error}. "
              "No stop command sent; an in-flight command may continue.",
              flush=True)
        return 1
    finally:
        if controller is not None:
            controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
