import re
from unittest.mock import patch

from mirobot_pick_place.mirobot_controller import MirobotController


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
        command = data.decode("utf-8").strip()
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


class TestLogger:

    def info(self, message):
        print("[INFO]", message)

    def error(self, message):
        print("[ERROR]", message)


def main():

    fake_serial = FakeSerial()
    controller = None

    try:

        # Replace only the serial device and sleep function.
        # The actual WLKATA SDK remains in use.
        with patch("serial.Serial", return_value=fake_serial):
            with patch("time.sleep", return_value=None):

                controller = MirobotController(
                    logger=TestLogger(),
                    simulation_mode=False,
                    serial_port="/dev/FAKE_MIROBOT",
                    baudrate=115200,
                    motion_timeout_sec=5.0
                )

                # Test Cartesian movement
                controller.move_to(
                    "PICK",
                    {
                        "x": 100.0,
                        "y": 0.0,
                        "z": 50.0,
                        "rx": 0.0,
                        "ry": 0.0,
                        "rz": 0.0
                    }
                )

                # Test vacuum ON
                controller.set_pump(True)

                # Test vacuum OFF
                controller.set_pump(False)

        commands = fake_serial.writes

        expected_move = (
            "M20G90G00X100.0Y0.0Z50.0"
            "A0.0B0.0C0.0"
        )

        assert expected_move in commands, (
            "Cartesian movement command is incorrect."
        )

        assert "M3 S1000" in commands, (
            "Vacuum ON command is missing."
        )

        assert "M3 S0" in commands, (
            "Vacuum OFF command is missing."
        )

        assert fake_serial.is_open, (
            "Unexpected serial close before cleanup."
        )

        print("\n===== TEST RESULTS =====")
        print("[PASS] SDK Cartesian movement")
        print("[PASS] Absolute coordinate G-code")
        print("[PASS] Vacuum pump ON")
        print("[PASS] Vacuum pump OFF")
        print("[PASS] Idle status parsing")
        print("[PASS] Fake serial communication")
        print("========================")
        print("ALL MOCK TESTS PASSED")

    finally:

        if controller is not None:
            controller.close()

        print("[TEST] Fake serial test finished.")


if __name__ == "__main__":
    main()
