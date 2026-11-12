import time
import re
import math
import threading

from .configuration import finite_number, validate_pose


class MirobotController:

    def __init__(
        self,
        logger,
        simulation_mode=True,
        serial_port="/dev/ttyUSB0",
        baudrate=115200,
        motion_timeout_sec=60.0,
        simulation_step_delay_sec=0.5,
        startup_observation_sec=3.0,
        position_tolerance_mm=1.0,
        orientation_tolerance_deg=1.0,
        motion_feedrate_mm_min=None,
        workspace_bounds=None,
        log_movement_targets=True,
        trace_callback=None
    ):
        self.logger = logger
        self.simulation_mode = simulation_mode
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.motion_timeout_sec = motion_timeout_sec
        self.log_movement_targets = log_movement_targets
        self.trace_callback = trace_callback
        self.operation_phase = "disconnected"
        self.last_response = None
        self.last_command = None
        self._next_wait_report = 0.0
        self.simulation_step_delay_sec = simulation_step_delay_sec

        self.startup_observation_sec = startup_observation_sec
        self.ready = False
        self.last_state = "Unknown"
        self._rx_buffer = ""
        self.last_status = None
        self.position_tolerance_mm = float(position_tolerance_mm)
        self.orientation_tolerance_deg = float(orientation_tolerance_deg)
        if not all(math.isfinite(value) and value >= 0 for value in (
            self.position_tolerance_mm, self.orientation_tolerance_deg
        )):
            raise ValueError(
                "Coordinate tolerances must be finite and nonnegative."
            )
        self._operation_lock = threading.Lock()
        self.workspace_bounds = workspace_bounds
        self.motion_feedrate_mm_min = motion_feedrate_mm_min
        if motion_feedrate_mm_min is not None:
            self.motion_feedrate_mm_min = finite_number(
                motion_feedrate_mm_min, "motion_feedrate_mm_min", positive=True
            )
            if self.motion_feedrate_mm_min > 3000:
                raise ValueError("Feedrate must be <= 3000 mm/min.")

        self.robot = None
        self.ser = None

        if self.simulation_mode:
            self.operation_phase = "simulation"
            self.logger.info("Mirobot simulation controller initialized.")
        else:
            self.connect()

    # ----------------------------------
    # Serial connection
    # ----------------------------------
    def connect(self):

        self.close()
        self.operation_phase = "connecting"
        self._next_wait_report = time.monotonic() + 5.0
        self.last_state = "Unknown"
        self.last_status = None
        self._rx_buffer = ""

        try:
            import serial
            from Mirobot_robot.Mirobot_UART import Mirobot_UART

        except ImportError as error:
            raise RuntimeError(
                "WLKATA SDK 또는 pyserial을 찾을 수 없습니다. "
                "SDK 설치가 필요합니다."
            ) from error

        # Configure control lines before opening. This does not guarantee
        # that the USB driver/device will avoid a reset.
        self.ser = serial.Serial()
        self.ser.port = self.serial_port
        self.ser.baudrate = self.baudrate
        self.ser.timeout = 0.2
        self.ser.write_timeout = 1
        self.ser.rtscts = False
        self.ser.dsrdtr = False
        self.ser.dtr = False
        self.ser.rts = False

        try:
            self.ser.open()
            self.robot = Mirobot_UART(block_flag=False, message_flag=False)
            self.robot.init(self.ser, -1)
            sdk_send = self.robot.sendMsg

            def traced_send(command):
                self.last_command = command
                self._trace("TX", command)
                sdk_send(command)

            self.robot.sendMsg = traced_send
            self.operation_phase = "startup"
            startup_fault = None
            deadline = time.monotonic() + self.startup_observation_sec
            while time.monotonic() < deadline:
                line = self._read_line()
                if line:
                    self.logger.info(f"Startup RX: {line}")
                    try:
                        self._check_response(line)
                    except RuntimeError as error:
                        # A boot during initial connection is expected; an
                        # alarm/error must remain visible and inhibit control.
                        if "WLKATA Robot started" not in line:
                            startup_fault = error
            try:
                if startup_fault is not None:
                    raise startup_fault
                self.wait_until_idle()
                self.operation_phase = "idle"
            except (RuntimeError, TimeoutError) as error:
                self.operation_phase = "not_ready"
                self.logger.info(f"Connected, but not ready: {error}")
        except Exception:
            self.close()
            raise

        self.logger.info(f"Connected to Mirobot: {self.serial_port}")

    def _trace(self, event, message):
        if self.trace_callback is not None:
            self.trace_callback(event, message)

    def _query_status(self):
        self._trace("TX", "?")
        self.ser.write(b"?\r\n")

    def _report_wait(self):
        now = time.monotonic()
        if now >= self._next_wait_report:
            self.logger.info(
                f"Waiting: phase={self.operation_phase}, "
                f"state={self.last_state} (no retry/extra motion sent)"
            )
            self._next_wait_report = now + 5.0

    def _read_line(self):
        if self.ser is None or not self.ser.is_open:
            self.ready = False
            raise RuntimeError("Serial connection is closed.")
        if "\n" not in self._rx_buffer:
            try:
                data = self.ser.readline()
            except Exception:
                self.ready = False
                raise
            self._rx_buffer += data.decode("utf-8", errors="replace")
        if "\n" not in self._rx_buffer:
            return ""
        line, self._rx_buffer = self._rx_buffer.split("\n", 1)
        if line.strip():
            self.last_response = line.strip()
            self._trace("RX", self.last_response)
        return line.strip()

    def _check_response(self, line):
        if "WLKATA Robot started" in line:
            self.last_state = "Unknown"
            self.last_status = None
            self.ready = False
            raise RuntimeError(
                "Robot restarted; readiness must be checked again."
            )
        if line.lower().startswith(("error", "alarm:")):
            self.ready = False
            raise RuntimeError(f"Robot response: {line}")
        # Validate a complete status frame; never accept a partial '<Idle'.
        number = r"-?\d+(?:\.\d+)?"
        pattern = (
            r"<(\w+),Angle\(ABCDXYZ\):" + (number + ",") * 7
            + r"Cartesian coordinate\(XYZ RxRyRz\):"
            + (number + ",") * 6
            + "Pump PWM:" + number + ",Valve PWM:" + number
            + ",Motion_MODE:" + number + ">"
        )
        match = re.fullmatch(pattern, line)
        if not match:
            return None
        self.last_state = match.group(1)
        coordinates = line.split("Cartesian coordinate(XYZ RxRyRz):", 1)[1]
        values = coordinates.split(",")
        self.last_status = dict(zip(
            ("x", "y", "z", "rx", "ry", "rz"),
            (float(value) for value in values[:6])
        ))
        self.last_status["pump"] = float(values[6].split(":", 1)[1])
        self.last_status["valve"] = float(values[7].split(":", 1)[1])
        if self.last_state == "Alarm":
            self.ready = False
            raise RuntimeError(
                "Robot is in Alarm; automatic homing is disabled."
            )
        return self.last_state

    def wait_until_idle(self, timeout=None, reject_busy=False):
        if self.simulation_mode:
            return
        self.ready = False
        if timeout is None:
            timeout = self.motion_timeout_sec
        deadline = time.monotonic() + timeout
        idle_count = 0
        try:
            while time.monotonic() < deadline:
                self._report_wait()
                # Do not flush RX: it may contain restart/error responses.
                self._query_status()
                response_deadline = min(deadline, time.monotonic() + 1.0)
                while time.monotonic() < response_deadline:
                    line = self._read_line()
                    if not line:
                        continue
                    state = self._check_response(line)
                    if state is None:
                        continue
                    if reject_busy and state != "Idle":
                        raise RuntimeError(
                            f"Robot is {state}; command refused."
                        )
                    idle_count = idle_count + 1 if state == "Idle" else 0
                    break
                else:
                    idle_count = 0
                if idle_count >= 3:
                    self.ready = True
                    return
                time.sleep(0.1)
        except Exception:
            self.ready = False
            raise
        raise TimeoutError(
            f"Robot did not become Idle. Last state: {self.last_state}"
        )

    def _require_ready(self):
        if self.ser is None or not self.ser.is_open:
            self.ready = False
        if not self.ready:
            raise RuntimeError(
                "Robot is not ready; movement/pump command blocked."
            )
        # Refresh status before every operation, including queued boot/errors.
        self.wait_until_idle(reject_busy=True)

    def _drain_pending(self, allow_alarm=False):
        # Consume, rather than discard, old acknowledgments and fault messages.
        deadline = time.monotonic() + self.motion_timeout_sec
        while self._rx_buffer or self.ser.in_waiting:
            self._report_wait()
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Could not establish a clean command boundary."
                )
            line = self._read_line()
            if line:
                state = self._check_before_command(line, allow_alarm)
                allowed = ("Idle", "Alarm") if allow_alarm else ("Idle",)
                if state is not None and state not in allowed:
                    raise RuntimeError(
                        "Robot stopped being Idle before command."
                    )

    def _check_before_command(self, line, allow_alarm=False):
        try:
            return self._check_response(line)
        except RuntimeError:
            if allow_alarm and line.startswith("<Alarm,"):
                return "Alarm"
            raise

    def _wait_command_completion(self, deadline):
        # Do not query until OK: '?' may also produce an acknowledgment.
        self.operation_phase = "waiting_ack"
        while time.monotonic() < deadline:
            self._report_wait()
            line = self._read_line()
            self._check_response(line)
            if line == "ok":
                break
        else:
            raise TimeoutError(
                "Command acknowledgment 'ok' not received. "
                f"Last state: {self.last_state}; phase=waiting_ack"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Command completion deadline expired.")
        self.operation_phase = "waiting_idle"
        self.wait_until_idle(timeout=remaining)

    def home(self):
        """Explicit homing only; never invoked by connection or a goal."""
        if self.simulation_mode:
            self.logger.info("[SIMULATION] Explicit homing completed.")
            return
        with self._operation_lock:
            self.ready = False
            self.operation_phase = "homing_preflight"
            self._next_wait_report = time.monotonic() + 5.0
            try:
                self._query_status()
                deadline = time.monotonic() + self.motion_timeout_sec
                while time.monotonic() < deadline:
                    state = self._check_before_command(
                        self._read_line(), allow_alarm=True
                    )
                    if state is not None:
                        if state not in ("Idle", "Alarm"):
                            raise RuntimeError(
                                "Robot is busy; homing refused."
                            )
                        break
                else:
                    raise TimeoutError("No status before homing.")
                self._drain_pending(allow_alarm=True)
                if (self.last_status is None
                        or self.last_status["pump"] != 0
                        or self.last_status["valve"] != 0):
                    raise RuntimeError("Homing requires pump and valve PWM 0.")
                self.robot.sendMsg("$H")
                self._wait_command_completion(deadline)
                if self.workspace_bounds is not None:
                    validate_pose(self.last_status, self.workspace_bounds)
                self.operation_phase = "idle"
            except Exception:
                self.ready = False
                self.operation_phase = "failed"
                raise

    def _verify_final_status(self, expected_pose=None, expected_pump=None):
        if self.last_status is None:
            raise RuntimeError("Final robot status is missing.")
        if expected_pose is not None:
            for axis in ("x", "y", "z", "rx", "ry", "rz"):
                error = abs(self.last_status[axis] - expected_pose[axis])
                tolerance = self.position_tolerance_mm
                if axis in ("rx", "ry", "rz"):
                    error = abs((error + 180.0) % 360.0 - 180.0)
                    tolerance = self.orientation_tolerance_deg
                if error > tolerance:
                    raise RuntimeError(
                        f"Final coordinate mismatch: {axis}, "
                        f"expected={expected_pose[axis]}, "
                        f"reported={self.last_status[axis]}"
                    )
        if (expected_pump is not None
                and self.last_status["pump"] != expected_pump):
            raise RuntimeError(
                f"Final pump PWM mismatch: expected={expected_pump}, "
                f"reported={self.last_status['pump']}"
            )

    def _send_operation(
        self, operation, expected_pose=None, expected_pump=None
    ):
        with self._operation_lock:
            try:
                self.operation_phase = "preflight"
                self._next_wait_report = time.monotonic() + 5.0
                self._require_ready()
                if self.workspace_bounds is not None:
                    validate_pose(self.last_status, self.workspace_bounds)
                self.ready = False
                self.operation_phase = "draining_rx"
                self._drain_pending()
                deadline = time.monotonic() + self.motion_timeout_sec
                self.operation_phase = "sending"
                operation()
                self._wait_command_completion(deadline)
                self.operation_phase = "verifying"
                self._verify_final_status(expected_pose, expected_pump)
                self.operation_phase = "idle"
            except Exception:
                self.ready = False
                self.operation_phase = "failed"
                raise

    # ----------------------------------
    # Cartesian absolute movement
    # ----------------------------------
    def move_to(self, name, pose):

        pose = validate_pose(pose, self.workspace_bounds)

        if self.log_movement_targets:
            self.logger.info(
                f"Moving to {name}: "
                f"X={pose['x']}, Y={pose['y']}, Z={pose['z']}, "
                f"RX={pose['rx']}, RY={pose['ry']}, RZ={pose['rz']}"
            )

        if self.simulation_mode:

            self.logger.info(
                f"[SIMULATION] Move command completed: {name}"
            )

            time.sleep(self.simulation_step_delay_sec)
            return

        # motion=0 : G00 rapid movement
        # position=0 : G90 absolute coordinates
        pose = {axis: float(pose[axis]) for axis in (
            "x", "y", "z", "rx", "ry", "rz"
        )}
        if not all(math.isfinite(value) for value in pose.values()):
            raise ValueError("Movement coordinates must be finite.")
        # Match the robot's millimetre/degree reporting precision. Tool-axis
        # trigonometry must not emit unbounded floating-point decimals.
        pose = validate_pose({axis: round(value, 3)
                              for axis, value in pose.items()},
                             self.workspace_bounds)
        if self.motion_feedrate_mm_min is not None:
            # Put F in every G01 command; do not rely on a previous session's
            # modal speed or on G00, which ignores the feedrate.
            coordinates = "".join(
                f"{letter}{pose[axis]}" for letter, axis in zip(
                    "XYZABC", ("x", "y", "z", "rx", "ry", "rz")
                )
            )
            command = (f"M20G90G01{coordinates}"
                       f"F{self.motion_feedrate_mm_min}")
            self._send_operation(
                lambda: self.robot.sendMsg(command), expected_pose=pose
            )
            self.logger.info(f"Movement completed: {name}")
            return
        self._send_operation(lambda: self.robot.writecoordinate(
            0,
            0,
            x=pose["x"],
            y=pose["y"],
            z=pose["z"],
            a=pose["rx"],
            b=pose["ry"],
            c=pose["rz"]
        ), expected_pose=pose)

        self.logger.info(f"Movement completed: {name}")

    # ----------------------------------
    # Vacuum pump
    # ----------------------------------
    def set_pump(self, enabled):

        if enabled:
            self.logger.info("Vacuum pump ON")
        else:
            self.logger.info("Vacuum pump OFF")

        if self.simulation_mode:

            self.logger.info(
                f"[SIMULATION] Pump state: {'ON' if enabled else 'OFF'}"
            )

            time.sleep(self.simulation_step_delay_sec)
            return

        # SDK:
        # pump(1) = ON
        # pump(0) = OFF
        self._send_operation(
            lambda: self.robot.pump(1 if enabled else 0),
            expected_pump=1000 if enabled else 0
        )

    # ----------------------------------
    # Close serial
    # ----------------------------------
    def close(self):

        self.ready = False
        self.operation_phase = "closed"

        if self.ser is not None and self.ser.is_open:

            self.ser.close()
            self.logger.info("Serial connection closed.")
