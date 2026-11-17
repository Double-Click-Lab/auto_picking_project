"""Topic-to-fixed-plan adapter for the Mirobot notebook."""

import fcntl
import math
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path

import rclpy
import yaml
from ament_index_python.packages import get_package_share_directory
from mirobot_pick_place.configuration import controller_options, validate_config
from mirobot_pick_place.mirobot_controller import MirobotController
from mirobot_pick_place.sequence import build_plan, positions
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from .ledger import Ledger
from .protocol import (ProtocolError, canonical_json, parse_json_object,
                       validate_ack, validate_cancel, validate_command,
                       validate_heartbeat)


TOPICS = {
    "command": "/picking/robot/command",
    "events": "/picking/device_events",
    "status": "/picking/device_status",
    "heartbeat": "/picking/server_heartbeat",
    "ack": "/picking/result_ack",
    "cancel": "/picking/cancel",
}
AXES = ("x", "y", "z", "rx", "ry", "rz")


def load_fixed_plan(path, workspace_bounds=None):
    """Load the single locally configured plan and derive its 60 mm approaches."""
    with open(path, "r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise ValueError("fixed plan root must be a YAML mapping")
    if document.get("tool_axis_approach") is not True:
        raise ValueError("fixed plan requires tool_axis_approach: true")
    if document.get("motion_feedrate_mm_min") != 3000:
        raise ValueError("fixed plan feedrate must be exactly 3000 mm/min")
    poses = {}
    for name in ("home", "pick", "place"):
        pose = document.get(name)
        if not isinstance(pose, dict) or set(pose) != set(AXES):
            raise ValueError(f"fixed plan {name} must contain exactly {AXES}")
        checked = {}
        for axis in AXES:
            value = pose[axis]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
                raise ValueError(f"fixed plan {name}.{axis} must be finite numeric")
            checked[axis] = float(value)
            if workspace_bounds is not None:
                low, high = workspace_bounds[axis]
                if not low <= checked[axis] <= high:
                    raise ValueError(f"fixed plan {name}.{axis} outside configured workspace")
        poses[name] = checked
    height = document.get("approach_height_mm")
    if (isinstance(height, bool) or not isinstance(height, (int, float))
            or not math.isfinite(height) or height != 60):
        raise ValueError("fixed plan approach_height_mm must be exactly 60")
    poses["approach_height"] = float(height)
    plan_poses = positions(poses["home"], poses["pick"], poses["place"],
                           poses["approach_height"], tool_axis=True)
    if workspace_bounds is not None:
        for name in ("pick_approach", "place_approach"):
            for axis in AXES:
                low, high = workspace_bounds[axis]
                if not low <= plan_poses[name][axis] <= high:
                    raise ValueError(f"fixed plan {name}.{axis} outside configured workspace")
    poses.update(plan_poses)
    poses["motion_feedrate_mm_min"] = 3000.0
    poses["tool_axis_approach"] = True
    return poses


def command_rejection_reason(command, mode, heartbeat, heartbeat_age,
                             fault, busy, pending_results, now):
    """Pure protocol admission guard shared with unit tests."""
    if command["mode"] != mode:
        return "command.mode does not match adapter mode"
    if heartbeat is None or heartbeat_age > 5.0:
        return "matching server heartbeat is absent or older than 5 seconds"
    if abs(now - heartbeat["sent_at"]) > 1.0:
        return "server heartbeat clock differs by more than 1 second"
    if command["server_session_id"] != heartbeat["server_session_id"]:
        return "command server_session_id does not match latest heartbeat"
    if command["expires_at"] - now <= 2.0:
        return "command expires in 2 seconds or less"
    if fault:
        return "adapter fault is latched; operator inspection/recovery is required"
    if pending_results:
        return "a previous final result is still awaiting server acknowledgement"
    if busy:
        return "another robot command is active"
    return ""


def cancel_matches(cancel, active_record):
    if not active_record:
        return False
    command = active_record["command"]
    return (cancel["device"] == "robot"
            and cancel["command_id"] == command["command_id"]
            and cancel["server_session_id"] == command["server_session_id"])


class MirobotTopicNode(Node):
    """Accept server commands and run only the fixed local 10-step plan."""

    def __init__(self):
        super().__init__("mirobot_topic_adapter")
        self.declare_parameter("mode", "simulation")
        self.declare_parameter("plan_timeout_sec", 45.0)
        self.declare_parameter("database_path", os.path.expanduser(
            "~/.ros/picking_mirobot_adapter.sqlite3"))
        self.declare_parameter("controller_config", "")
        self.declare_parameter("plan_file", "")
        self.mode = str(self.get_parameter("mode").value)
        self.plan_timeout_sec = float(self.get_parameter("plan_timeout_sec").value)
        if self.mode not in ("simulation", "real"):
            raise ValueError("mode must be simulation or real")
        if not math.isfinite(self.plan_timeout_sec) or self.plan_timeout_sec <= 1:
            raise ValueError("plan_timeout_sec must be finite and greater than 1")
        if os.environ.get("ROS_DOMAIN_ID") != "7" or os.environ.get("ROS_LOCALHOST_ONLY") != "0":
            raise RuntimeError("set ROS_DOMAIN_ID=7 and ROS_LOCALHOST_ONLY=0 before starting")

        default_config = os.path.join(
            get_package_share_directory("mirobot_pick_place"), "config", "poses.yaml")
        config_path = str(self.get_parameter("controller_config").value) or default_config
        with open(config_path, "r", encoding="utf-8") as stream:
            self.controller_config = validate_config(yaml.safe_load(stream))
        if self.controller_config["simulation_mode"] is not (self.mode == "simulation"):
            raise ValueError("adapter mode and existing controller simulation_mode differ")
        if self.mode == "real":
            if not self.controller_config["hardware_enable"]:
                raise ValueError("real mode requires hardware_enable: true")
            if not self.controller_config["coordinates_confirmed"]:
                raise ValueError("real mode requires coordinates_confirmed: true")
            if self.controller_config["tool_axis_approach"] is not True:
                raise ValueError("real controller config must retain tool_axis_approach: true")

        plan_path = str(self.get_parameter("plan_file").value)
        if not plan_path:
            plan_path = os.path.join(
                get_package_share_directory("picking_mirobot_adapter"),
                "config", "fixed_robot_plan.yaml")
        self.poses = load_fixed_plan(plan_path, self.controller_config["workspace_bounds"])
        if self.mode == "real":
            configured_home = self.controller_config["poses"]["home"]
            if any(abs(self.poses["home"][axis] - configured_home[axis]) > 1e-9
                   for axis in AXES):
                raise ValueError("real controller config poses.home must match fixed plan HOME")

        db_path = os.path.expanduser(str(self.get_parameter("database_path").value))
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._lock_file = open(db_path + ".lock", "a+", encoding="utf-8")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another adapter process owns the SQLite ledger") from exc
        self.ledger = Ledger(db_path)
        self.ledger.fail_unfinished_on_restart()
        self.session_id = str(uuid.uuid4())
        self.callback_group = MutuallyExclusiveCallbackGroup()
        self.qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=10,
                              reliability=ReliabilityPolicy.RELIABLE,
                              durability=DurabilityPolicy.VOLATILE)
        self.pub_events = self.create_publisher(String, TOPICS["events"], self.qos)
        self.pub_status = self.create_publisher(String, TOPICS["status"], self.qos)
        self.sub_command = self.create_subscription(
            String, TOPICS["command"], self._on_command, self.qos,
            callback_group=self.callback_group)
        self.sub_heartbeat = self.create_subscription(
            String, TOPICS["heartbeat"], self._on_heartbeat, self.qos,
            callback_group=self.callback_group)
        self.sub_ack = self.create_subscription(
            String, TOPICS["ack"], self._on_ack, self.qos,
            callback_group=self.callback_group)
        self.sub_cancel = self.create_subscription(
            String, TOPICS["cancel"], self._on_cancel, self.qos,
            callback_group=self.callback_group)

        self.heartbeat = None
        self.heartbeat_received_mono = None
        self.heartbeat_problem = "waiting for server heartbeat"
        self.active = None
        self.controller = None
        self.controller_ready = self.mode == "simulation"
        self.startup_fault = ""
        self._shutdown_event = threading.Event()
        self._controller_init_done = threading.Event()
        self._worker_done = queue.SimpleQueue()
        self._last_status = 0.0
        self._last_result_republish = 0.0
        self.state = "fault" if self.ledger.get_fault() else "initializing"
        self.detail = ("Restart left unresolved command outcome; inspect robot and use documented recovery"
                       if self.ledger.get_fault() else self.heartbeat_problem)
        if self.mode == "real" and not self.ledger.get_fault():
            self._controller_thread = threading.Thread(
                target=self._initialize_hardware_controller, daemon=True,
                name="mirobot-controller-init")
            self._controller_thread.start()
        else:
            self._controller_thread = None
            self._controller_init_done.set()
        self._timer = self.create_timer(0.1, self._tick,
                                       callback_group=self.callback_group)
        self.get_logger().info(
            f"Fixed-plan topic adapter started in {self.mode} mode; plan={plan_path}")

    def _initialize_hardware_controller(self):
        try:
            # The upstream controller imports the vendor SDK by its top-level
            # package name. Add the checked-in SDK location when this workspace
            # is run from source, without changing the legacy controller.
            candidates = [
                Path.cwd() / "src" / "Wlkata_Mirobot_Ros2" / "wlkatapython",
                Path(__file__).resolve().parents[3] / "src"
                / "Wlkata_Mirobot_Ros2" / "wlkatapython",
            ]
            for candidate in candidates:
                if (candidate / "Mirobot_robot" / "Mirobot_UART.py").is_file():
                    if str(candidate) not in sys.path:
                        sys.path.insert(0, str(candidate))
                    break
            options = controller_options(self.controller_config)
            # The approved final plan always uses this explicit feedrate.
            options["motion_feedrate_mm_min"] = 3000.0
            controller = MirobotController(
                logger=self.get_logger(), **options)
            if self._shutdown_event.is_set():
                controller.close()
                return
            self.controller = controller
            self.controller_ready = bool(controller.ready)
            if not self.controller_ready:
                self.startup_fault = (
                    "Mirobot is connected but not ready/Idle. No homing or motion was sent.")
        except Exception as exc:  # reported through status, never auto-retried
            self.startup_fault = f"Mirobot controller initialization failed: {type(exc).__name__}: {str(exc)[:300]}"
        finally:
            self._controller_init_done.set()

    def _decode(self, msg, validator, label):
        try:
            return validator(parse_json_object(msg.data))
        except (ProtocolError, ValueError, TypeError) as exc:
            self.get_logger().warning(f"Ignored invalid {label}: {exc}")
            return None

    def _on_heartbeat(self, msg):
        value = self._decode(msg, validate_heartbeat, "server heartbeat")
        if value is None:
            return
        now = time.time()
        if abs(now - value["sent_at"]) > 1.0:
            self.heartbeat_problem = "server heartbeat clock differs by more than 1 second"
            self.get_logger().error(self.heartbeat_problem)
            self.heartbeat = None
            self.heartbeat_received_mono = None
            if self.active:
                self._request_abort("server heartbeat clock skew exceeded 1 second")
            return
        old_session = self.heartbeat["server_session_id"] if self.heartbeat else None
        self.heartbeat = value
        self.heartbeat_received_mono = time.monotonic()
        self.heartbeat_problem = ""
        if old_session and old_session != value["server_session_id"] and self.active:
            self._request_abort("server session changed during command")

    def _on_ack(self, msg):
        ack = self._decode(msg, validate_ack, "result ack")
        if ack and self.ledger.acknowledge(ack):
            self.get_logger().info(f"Result acknowledged for {ack['command_id']}")

    def _heartbeat_fresh(self):
        return (self.heartbeat is not None
                and self.heartbeat_received_mono is not None
                and time.monotonic() - self.heartbeat_received_mono <= 5.0)

    def _on_command(self, msg):
        command = self._decode(msg, validate_command, "robot command")
        if command is None:
            return
        existing = self.ledger.command_row(command["command_id"])
        if existing is None:
            reason = self._admission_error(command)
            if reason:
                self.get_logger().warning(f"Command not accepted: {reason}")
                return
        outcome, accepted, result = self.ledger.get_or_create(command)
        if outcome == "conflict":
            self.get_logger().error(
                "Protocol error: command_id reused with changed content; original record retained")
            return
        if outcome == "duplicate":
            self._publish_event(result if result else accepted)
            return

        self._publish_event(accepted)
        now = time.time()
        remaining = command["expires_at"] - now - 1.0
        deadline = time.monotonic() + min(self.plan_timeout_sec, remaining)
        self.ledger.mark_running(command["command_id"])
        record = {
            "command": command,
            "deadline_mono": deadline,
            "cancel_event": threading.Event(),
            "terminal_recorded": False,
            "worker": None,
        }
        self.active = record
        self.state = "busy"
        self.detail = "Accepted; running the fixed local 10-step plan"
        record["worker"] = threading.Thread(
            target=self._run_plan_worker, args=(record,), daemon=True,
            name=f"mirobot-plan-{command['command_id'][:8]}")
        record["worker"].start()

    def _admission_error(self, command):
        age = (time.monotonic() - self.heartbeat_received_mono
               if self.heartbeat_received_mono is not None else float("inf"))
        reason = command_rejection_reason(
            command, self.mode, self.heartbeat, age, self.ledger.get_fault(),
            bool(self.active), self.ledger.pending_result_count(), time.time())
        if reason:
            return reason
        if not self._controller_init_done.is_set():
            return "Mirobot controller is still initializing"
        if self.startup_fault:
            return self.startup_fault
        if not self.controller_ready:
            return "Mirobot controller is not ready; explicit operator initialization is required"
        return ""

    def _run_plan_worker(self, record):
        command = record["command"]
        try:
            if self.mode == "real":
                controller = self.controller
                if controller is None or not controller.ready:
                    raise RuntimeError(
                        "Mirobot is not ready/Idle; refusing to start the fixed plan")
            else:
                controller = None
            steps = build_plan(self.poses)
            for index, step in enumerate(steps, start=1):
                self._check_worker_abort(record)
                self.get_logger().info(f"[{index}/10] {step.name}")
                if self.mode == "real":
                    step.execute(controller, pumps=True)
                    if not controller.ready:
                        raise RuntimeError(
                            f"Controller readiness lost after step {index}; physical state unconfirmed")
                else:
                    # Simulation is a pure software stub: never instantiate or
                    # call the serial controller in this mode.
                    time.sleep(0.05)
                self._check_worker_abort(record)
            self._worker_done.put((record, True,
                                   "Fixed 10-step plan completed; controller verified each operation.",
                                   False))
        except Exception as exc:
            detail = f"Fixed plan failed: {type(exc).__name__}: {str(exc)[:350]}"
            unknown = self.mode == "real"
            if unknown:
                detail += "; physical stop/state is unconfirmed; no automatic retry or return movement."
            self._worker_done.put((record, False, detail[:500], unknown))

    @staticmethod
    def _check_worker_abort(record):
        if record["cancel_event"].is_set():
            raise RuntimeError("execution cancelled at a plan-step boundary")
        if time.monotonic() >= record["deadline_mono"]:
            raise TimeoutError("local plan deadline reached")

    def _on_cancel(self, msg):
        cancel = self._decode(msg, validate_cancel, "cancel")
        if cancel and cancel_matches(cancel, self.active):
            self._request_abort("server cancel: " + cancel["reason"])

    def _request_abort(self, reason):
        record = self.active
        if not record:
            return
        record["cancel_event"].set()
        unknown = self.mode == "real"
        if not record["terminal_recorded"]:
            if unknown:
                detail = (reason + "; cancellation is checked between plan steps; a current serial operation "
                          "may continue and physical stop is unconfirmed.")[:500]
            else:
                detail = reason + "; software simulation only, no physical robot command was sent."
            event = self.ledger.finish(record["command"], False, detail,
                                       unknown=unknown)
            record["terminal_recorded"] = True
            self._publish_event(event)
        if unknown:
            self.ledger.latch_fault()
            self.state = "fault"
            self.detail = reason + "; physical stop is unconfirmed"
        else:
            self.state = "initializing"
            self.detail = reason + "; simulation cancelled"
        self.get_logger().error(self.detail)

    def _finish_worker(self, record, success, detail, unknown):
        if self.active is not record:
            return
        if not record["terminal_recorded"]:
            event = self.ledger.finish(record["command"], success, detail,
                                       unknown=unknown)
            record["terminal_recorded"] = True
            self._publish_event(event)
            if unknown:
                self.ledger.latch_fault()
        self.active = None
        if self.ledger.get_fault():
            self.state = "fault"
        else:
            self.state = "ready" if self._heartbeat_fresh() and self.ledger.pending_result_count() == 0 else "initializing"
        self.detail = detail[:500]

    def _tick(self):
        now_mono = time.monotonic()
        if now_mono - self._last_status >= 1.0:
            self._publish_status()
            self._last_status = now_mono
        if now_mono - self._last_result_republish >= 1.0:
            for event in self.ledger.unacked_results():
                self._publish_event(event)
            self._last_result_republish = now_mono

        if self.mode == "real" and self._controller_init_done.is_set() and self.startup_fault:
            self.state = "fault"
            self.detail = self.startup_fault
        if self.active:
            age = (now_mono - self.heartbeat_received_mono
                   if self.heartbeat_received_mono is not None else float("inf"))
            command = self.active["command"]
            abort_reason = ""
            if self.heartbeat is None or age > 5.0:
                abort_reason = "server heartbeat lost for more than 5 seconds"
            elif self.heartbeat["server_session_id"] != command["server_session_id"]:
                abort_reason = "server heartbeat session changed"
            elif now_mono >= self.active["deadline_mono"]:
                abort_reason = "local command/plan deadline reached"
            if abort_reason:
                self._request_abort(abort_reason)

        while True:
            try:
                record, success, detail, unknown = self._worker_done.get_nowait()
            except queue.Empty:
                break
            self._finish_worker(record, success, detail, unknown)

        if self.ledger.get_fault():
            self.state = "fault"
        elif not self.active:
            if self.mode == "real" and self.startup_fault:
                self.state = "fault"
                self.detail = self.startup_fault
            elif not self._heartbeat_fresh() or self.ledger.pending_result_count():
                self.state = "initializing"
                if not self.heartbeat_problem:
                    self.detail = "waiting for recent server heartbeat or result acknowledgement"
            elif self.mode == "real" and (not self._controller_init_done.is_set()
                                          or not self.controller_ready):
                self.state = "initializing"
                self.detail = self.startup_fault or "waiting for Mirobot controller readiness"
            else:
                self.state = "ready"
                self.detail = "ready to execute the fixed local plan"

    def _publish_event(self, event):
        message = String()
        message.data = canonical_json(event)
        self.pub_events.publish(message)

    def _publish_status(self):
        status = {
            "v": 1, "device_id": "mirobot_laptop", "device": "robot",
            "session_id": self.session_id, "mode": self.mode,
            "state": self.state,
            "active_command_id": self.active["command"]["command_id"] if self.active else None,
            "detail": str(self.detail)[:500], "sent_at": time.time(),
        }
        message = String()
        message.data = canonical_json(status)
        self.pub_status.publish(message)

    def destroy_node(self):
        try:
            self._shutdown_event.set()
            if self.active:
                self._request_abort("adapter shutdown during plan; outcome unconfirmed")
                worker = self.active.get("worker")
                if worker:
                    worker.join(timeout=0.2)
            # Do not close the serial port underneath an in-flight blocking
            # controller operation. Process shutdown will release it if needed.
            if self.controller is not None and not (
                    self.active and self.active.get("worker")
                    and self.active["worker"].is_alive()):
                self.controller.close()
            self.ledger.close()
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
        finally:
            super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = MirobotTopicNode()
        from rclpy.executors import MultiThreadedExecutor
        executor = MultiThreadedExecutor(num_threads=4)
        executor.add_node(node)
        executor.spin()
    except (ValueError, OSError, RuntimeError) as exc:
        if node:
            node.get_logger().fatal(str(exc))
        else:
            print(f"mirobot topic adapter startup failed: {exc}")
        raise
    finally:
        if node:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
