"""Explicitly clear a latched unknown-outcome fault after physical inspection."""

import argparse
import fcntl
import os

from .ledger import Ledger


def main(args=None):
    parser = argparse.ArgumentParser(description="Acknowledge operator inspection; this does not move the robot")
    parser.add_argument("--database-path", default=os.path.expanduser("~/.ros/picking_mirobot_adapter.sqlite3"))
    parser.add_argument("--confirm-physical-inspection", action="store_true")
    options = parser.parse_args(args)
    if not options.confirm_physical_inspection:
        parser.error("requires --confirm-physical-inspection after inspecting the robot and work area")
    os.makedirs(os.path.dirname(os.path.abspath(options.database_path)), exist_ok=True)
    lock_file = open(options.database_path + ".lock", "a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("stop the adapter process before recovery") from exc
        ledger = Ledger(options.database_path)
        ledger.clear_fault_after_inspection()
        ledger.close()
        print("Fault latch cleared. No robot command was sent; restart adapter after confirming physical safety.")
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


if __name__ == "__main__":
    main()
