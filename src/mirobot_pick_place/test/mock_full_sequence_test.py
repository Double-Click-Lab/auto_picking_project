import os
import tempfile
from pathlib import Path
import re
from unittest.mock import patch

import yaml
import rclpy

from mirobot_pick_place.pick_place_node import PickPlaceNode


class FakeSerial:

    def __init__(self):
        self.is_open = True
        self.writes = []
        self.response = b""
        self.pose = [0.0] * 6
        self.pump = 0

    @property
    def in_waiting(self):
        return len(self.response)

    def write(self, data):
        command = data.decode().strip()
        self.writes.append(command)

        if command.startswith("M20"):
            self.pose = [float(value) for value in re.findall(
                r"[XYZABC](-?\d+(?:\.\d+)?)", command
            )]
            self.response += b"ok\r\n"
        elif command.startswith("M3 S"):
            self.pump = int(command.split("S")[1])
            self.response += b"ok\r\n"
        elif command == "?":
            coordinates = ",".join(str(value) for value in self.pose)
            self.response += (
                "<Idle,Angle(ABCDXYZ):0,0,0,0,0,0,0,"
                f"Cartesian coordinate(XYZ RxRyRz):{coordinates},"
                f"Pump PWM:{self.pump},Valve PWM:0,Motion_MODE:0>\n"
            ).encode()

        return len(data)

    def readline(self):
        result = self.response
        self.response = b""
        return result

    def flushInput(self):
        self.response = b""

    def flushOutput(self):
        pass

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False


def main():

    fake_serial = FakeSerial()
    node = None
    temp_config = None

    try:
        # Load original configuration
        source = (
            Path(__file__).resolve().parents[1]
            / "config"
            / "poses.yaml"
        )

        with open(source, "r", encoding="utf-8") as file:
            config = yaml.safe_load(file)

        # Enable the hardware code path only in this
        # temporary test configuration.
        # Serial communication is replaced with FakeSerial.
        config["simulation_mode"] = False
        config["hardware_enable"] = True
        config["coordinates_confirmed"] = True
        # Test-only intervals, not physical robot workspace recommendations.
        config["workspace_bounds"] = {
            axis: [-500, 500] for axis in ("x", "y", "z", "rx", "ry", "rz")
        }
        config["pump_settle_sec"] = 0.0
        config["release_settle_sec"] = 0.0
        config["motion_timeout_sec"] = 5.0

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
            encoding="utf-8"
        ) as file:
            yaml.safe_dump(config, file)
            temp_config = file.name

        rclpy.init(
            args=[
                "--ros-args",
                "-p",
                f"config_file:={temp_config}"
            ]
        )

        # Patch serial and sleep.
        # No physical USB device is opened.
        with patch("serial.Serial", return_value=fake_serial):
            with patch("time.sleep", return_value=None):

                node = PickPlaceNode()
                node.run_sequence()

                actual = [
                    command for command in fake_serial.writes
                    if command.startswith("M20")
                    or command.startswith("M3 S")
                ]

                expected = [
                    "M20G90G01X0.0Y0.0Z0.0A0.0B0.0C0.0F100.0",
                    "M20G90G01X100.0Y0.0Z80.0A0.0B0.0C0.0F100.0",
                    "M20G90G01X100.0Y0.0Z50.0A0.0B0.0C0.0F100.0",
                    "M3 S1000",
                    "M20G90G01X100.0Y0.0Z80.0A0.0B0.0C0.0F100.0",
                    "M20G90G01X100.0Y100.0Z80.0A0.0B0.0C0.0F100.0",
                    "M20G90G01X100.0Y100.0Z50.0A0.0B0.0C0.0F100.0",
                    "M3 S0",
                    "M20G90G01X100.0Y100.0Z80.0A0.0B0.0C0.0F100.0",
                    "M20G90G01X0.0Y0.0Z0.0A0.0B0.0C0.0F100.0"
                ]

                assert actual == expected, (
                    f"Command sequence mismatch:\n"
                    f"Expected: {expected}\n"
                    f"Actual: {actual}"
                )

                print("\n===== FULL SEQUENCE TEST =====")
                print("[PASS] HOME")
                print("[PASS] PICK_APPROACH")
                print("[PASS] PICK")
                print("[PASS] PUMP ON")
                print("[PASS] LIFT")
                print("[PASS] PLACE_APPROACH")
                print("[PASS] PLACE")
                print("[PASS] PUMP OFF")
                print("[PASS] PLACE LIFT")
                print("[PASS] RETURN HOME")
                print("==============================")
                print("ALL FULL SEQUENCE TESTS PASSED")

    finally:

        if node is not None:
            node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()

        if temp_config and os.path.exists(temp_config):
            os.remove(temp_config)


if __name__ == "__main__":
    main()
