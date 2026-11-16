"""Nonblocking ROS topic adapter that delegates motion to /pick_place."""

import fcntl
import math
import os
import time
import uuid

import rclpy
import yaml
from action_msgs.msg import GoalStatus
from ament_index_python.packages import get_package_share_directory
from mirobot_pick_place.configuration import validate_config, validate_pose
from mirobot_pick_place.sequence import positions
from mirobot_pick_place_interfaces.action import PickPlace
from rclpy.action import ActionClient
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from std_srvs.srv import Trigger
from unique_identifier_msgs.msg import UUID as RosUUID

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


def load_pose_file(path, simulation_mode, workspace_bounds, tool_axis):
    """Load and validate the immutable-at-runtime robot pose document."""
    with open(path, "r", encoding="utf-8") as stream:
        doc = yaml.safe_load(stream)
    if not isinstance(doc, dict):
        raise ValueError("coordinate file root must be a YAML mapping")
    if not simulation_mode and (doc.get("simulation_only") is not False
                                or doc.get("coordinates_confirmed") is not True):
        raise ValueError("real mode requires simulation_only: false and coordinates_confirmed: true")
    poses = {}
    for name in ("home", "pick", "place"):
        pose = doc.get(name)
        if not isinstance(pose, dict):
            raise ValueError(f"coordinate file is missing {name}")
        checked = {}
        for axis in ("x", "y", "z", "rx", "ry", "rz"):
            value = pose.get(axis)
            try:
                valid_number = (not isinstance(value, bool)
                                and isinstance(value, (int, float))
                                and math.isfinite(value))
            except OverflowError:
                valid_number = False
            if not valid_number:
                raise ValueError(f"{name}.{axis} must be finite numeric")
            checked[axis] = float(value)
            if workspace_bounds is not None:
                low, high = workspace_bounds[axis]
                if not low <= checked[axis] <= high:
                    raise ValueError(f"{name}.{axis} outside configured workspace bounds")
        poses[name] = checked
    approach = doc.get("approach_height")
    try:
        valid_approach = (not isinstance(approach, bool)
                          and isinstance(approach, (int, float))
                          and math.isfinite(approach) and approach > 0)
    except OverflowError:
        valid_approach = False
    if not valid_approach:
        raise ValueError("approach_height must be a finite positive number")
    # Match the existing sequence's computed approach points and workspace rules.
    computed = positions(poses["home"], poses["pick"], poses["place"], float(approach), tool_axis=tool_axis)
    if workspace_bounds is not None:
        for name in ("pick_approach", "place_approach"):
            validate_pose(computed[name], workspace_bounds)
    poses["approach_height"] = float(approach)
    return poses


def command_rejection_reason(command, mode, heartbeat, heartbeat_age,
                             fault, busy, pending_results, now):
    """Pure admission guard shared by the node and its offline tests."""
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


def action_result_success(status, result_success):
    return status == GoalStatus.STATUS_SUCCEEDED and result_success is True


def active_safety_abort_reason(record, heartbeat, heartbeat_age, now_mono):
    if heartbeat is None or heartbeat_age > 5.0:
        return "server heartbeat lost for more than 5 seconds"
    if heartbeat["server_session_id"] != record["command"]["server_session_id"]:
        return "server heartbeat session changed"
    if now_mono >= record["deadline_mono"]:
        return "command expired; local action deadline reached"
    return ""


def cancel_matches(cancel, active_record):
    if not active_record:
        return False
    command = active_record["command"]
    return (cancel["device"] == "robot"
            and cancel["command_id"] == command["command_id"]
            and cancel["server_session_id"] == command["server_session_id"])


class MirobotTopicNode(Node):
    def __init__(self):
        super().__init__("mirobot_topic_adapter")
        self.declare_parameter("mode", "simulation")
        self.declare_parameter("simulation_action_backend", False)
        self.declare_parameter("action_timeout_sec", 45.0)
        self.declare_parameter("database_path", os.path.expanduser("~/.ros/picking_mirobot_adapter.sqlite3"))
        self.declare_parameter("controller_config", "")
        self.declare_parameter("coordinate_file", "")
        self.mode = str(self.get_parameter("mode").value)
        self.simulation_action_backend = bool(self.get_parameter("simulation_action_backend").value)
        self.action_timeout_sec = float(self.get_parameter("action_timeout_sec").value)
        if self.mode not in ("simulation", "real"):
            raise ValueError("mode must be simulation or real")
        if not math.isfinite(self.action_timeout_sec) or self.action_timeout_sec <= 1:
            raise ValueError("action_timeout_sec must be finite and greater than 1")
        if self.mode == "real" and self.simulation_action_backend:
            raise ValueError("simulation_action_backend cannot be enabled in real mode")
        if os.environ.get("ROS_DOMAIN_ID") != "7" or os.environ.get("ROS_LOCALHOST_ONLY") != "0":
            raise RuntimeError("set ROS_DOMAIN_ID=7 and ROS_LOCALHOST_ONLY=0 before starting")

        default_controller = os.path.join(get_package_share_directory("mirobot_pick_place"), "config", "poses.yaml")
        config_path = str(self.get_parameter("controller_config").value) or default_controller
        with open(config_path, "r", encoding="utf-8") as stream:
            self.controller_config = validate_config(yaml.safe_load(stream))
        expected_sim = self.mode == "simulation"
        if self.controller_config["simulation_mode"] is not expected_sim:
            raise ValueError("adapter mode and existing controller simulation_mode differ")
        coord_path = str(self.get_parameter("coordinate_file").value)
        if not coord_path:
            if self.mode == "real":
                raise ValueError("real mode requires an explicitly configured coordinate_file")
            coord_path = os.path.join(get_package_share_directory("picking_mirobot_adapter"), "config", "robot_poses.example.yaml")
        self.poses = load_pose_file(coord_path, expected_sim, self.controller_config["workspace_bounds"], self.controller_config["tool_axis_approach"])
        configured_home = self.controller_config["poses"]["home"]
        if any(abs(self.poses["home"][axis] - configured_home[axis]) > 1e-9
               for axis in ("x", "y", "z", "rx", "ry", "rz")):
            raise ValueError("coordinate file home must match controller_config poses.home")

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
        # Serialize small state transitions; network/action waits are futures
        # polled by the timer, never blocking callbacks.
        self.callback_group = MutuallyExclusiveCallbackGroup()
        self.qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=10,
                              reliability=ReliabilityPolicy.RELIABLE,
                              durability=DurabilityPolicy.VOLATILE)
        self.pub_events = self.create_publisher(String, TOPICS["events"], self.qos)
        self.pub_status = self.create_publisher(String, TOPICS["status"], self.qos)
        self.sub_command = self.create_subscription(String, TOPICS["command"], self._on_command, self.qos, callback_group=self.callback_group)
        self.sub_heartbeat = self.create_subscription(String, TOPICS["heartbeat"], self._on_heartbeat, self.qos, callback_group=self.callback_group)
        self.sub_ack = self.create_subscription(String, TOPICS["ack"], self._on_ack, self.qos, callback_group=self.callback_group)
        self.sub_cancel = self.create_subscription(String, TOPICS["cancel"], self._on_cancel, self.qos, callback_group=self.callback_group)
        self.client = ActionClient(self, PickPlace, "/pick_place", callback_group=self.callback_group)
        self.status_client = self.create_client(Trigger, "/mirobot/status", callback_group=self.callback_group)
        self.heartbeat = None
        self.heartbeat_received_mono = None
        self.heartbeat_problem = "waiting for server heartbeat"
        self.active = None
        self.pending = None
        self.state = "initializing"
        self.detail = "Loading durable state and waiting for server heartbeat"
        self._last_status = 0.0
        self._last_result_republish = 0.0
        self._timer = self.create_timer(0.1, self._tick, callback_group=self.callback_group)
        self.state = "fault" if self.ledger.get_fault() else "initializing"
        self.detail = "Restart left unresolved command outcome; inspect robot and use documented recovery" if self.ledger.get_fault() else self.heartbeat_problem
        self.get_logger().info(f"Adapter ready for {self.mode} mode; simulation_action_backend={self.simulation_action_backend}")

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
                self._abort_active("server heartbeat clock skew exceeded 1 second", unknown=True)
            return
        old_session = self.heartbeat["server_session_id"] if self.heartbeat else None
        self.heartbeat = value
        self.heartbeat_received_mono = time.monotonic()
        self.heartbeat_problem = ""
        if old_session and old_session != value["server_session_id"] and self.active:
            self._abort_active("server session changed during command", unknown=True)

    def _on_ack(self, msg):
        ack = self._decode(msg, validate_ack, "result ack")
        if ack and self.ledger.acknowledge(ack):
            self.get_logger().info(f"Result acknowledged for {ack['command_id']}")
            if self.ledger.pending_result_count() == 0 and self.state != "fault":
                self.state = "ready" if self._heartbeat_fresh() else "initializing"

    def _heartbeat_fresh(self):
        return (self.heartbeat is not None and self.heartbeat_received_mono is not None
                and time.monotonic() - self.heartbeat_received_mono <= 5.0)

    def _on_command(self, msg):
        command = self._decode(msg, validate_command, "robot command")
        if command is None:
            return
        existing = self.ledger.command_row(command["command_id"])
        # A retry must be recognized before current admission guards, including
        # the outstanding-result gate, so it can retrieve its saved event.
        if existing is None:
            reason = self._admission_error(command)
            if reason:
                self.get_logger().warning(f"Command not accepted: {reason}")
                return
        outcome, accepted, result = self.ledger.get_or_create(command)
        if outcome == "conflict":
            self.get_logger().error("Protocol error: command_id reused with changed content; original record retained")
            return
        if outcome == "duplicate":
            self._publish_event(result if result else accepted)
            return
        self._publish_event(accepted)
        now = time.time()
        monotonic_remaining = command["expires_at"] - now - 1.0
        self.ledger.mark_running(command["command_id"])
        self.pending = {
            "command": command,
            "deadline_mono": time.monotonic() + monotonic_remaining,
            "accepted_mono": time.monotonic(),
            "phase": "preflight",
            "service_future": None,
            "goal_future": None,
            "goal_handle": None,
            "result_future": None,
            "timeout_mono": None,
            "cancel_reason": None,
        }
        self.state = "busy"
        self.detail = "Accepted; checking /mirobot/status and /pick_place availability"

    def _admission_error(self, command):
        age = (time.monotonic() - self.heartbeat_received_mono
               if self.heartbeat_received_mono is not None else float("inf"))
        return command_rejection_reason(
            command, self.mode, self.heartbeat, age, self.ledger.get_fault(),
            bool(self.active or self.pending), self.ledger.pending_result_count(),
            time.time(),
        )

    def _on_cancel(self, msg):
        cancel = self._decode(msg, validate_cancel, "cancel")
        if not cancel:
            return
        active = self.active or self.pending
        if cancel_matches(cancel, active):
            self._abort_active("server cancel: " + cancel["reason"], unknown=True)

    def _abort_active(self, reason, *, unknown):
        active = self.active or self.pending
        if not active:
            return
        active["cancel_reason"] = reason
        handle = active.get("goal_handle")
        if handle is not None:
            try:
                active["cancel_future"] = handle.cancel_goal_async()
            except Exception as exc:  # cancellation transport itself can fail
                reason += f"; cancel request raised {type(exc).__name__}"
        if unknown:
            self.ledger.latch_fault()
        event = self.ledger.finish(active["command"], False,
                                   reason + "; cancellation requested where possible; physical stop is unconfirmed.", unknown=unknown)
        self._publish_event(event)
        self.active = None
        self.pending = None
        self.state = "fault" if unknown else "initializing"
        self.detail = reason + "; physical stop is unconfirmed"

    def _complete(self, success, detail):
        active = self.active or self.pending
        if not active:
            return
        event = self.ledger.finish(active["command"], success, detail)
        self._publish_event(event)
        self.active = None
        self.pending = None
        if not success and ("unknown" in detail.lower() or "timeout" in detail.lower()):
            self.ledger.latch_fault()
        self.state = "fault" if self.ledger.get_fault() else ("ready" if self._heartbeat_fresh() else "initializing")
        self.detail = detail[:500]

    def _send_goal(self, record):
        goal = PickPlace.Goal()
        goal.start = True
        for name in ("pick", "place"):
            for axis in ("x", "y", "z", "rx", "ry", "rz"):
                setattr(goal, f"{name}_{axis}", self.poses[name][axis])
        goal.approach_height = self.poses["approach_height"]
        ros_uuid = RosUUID()
        ros_uuid.uuid = list(uuid.UUID(record["command"]["command_id"]).bytes)
        record["goal_future"] = self.client.send_goal_async(goal, goal_uuid=ros_uuid)
        record["phase"] = "goal_response"
        remaining = record["deadline_mono"] - time.monotonic()
        record["timeout_mono"] = time.monotonic() + min(self.action_timeout_sec, remaining) - 0.5
        self.active = record
        self.pending = None
        self.detail = "PickPlace action goal sent"

    def _tick(self):
        now_mono = time.monotonic()
        if now_mono - self._last_status >= 1.0:
            self._publish_status()
            self._last_status = now_mono
        if now_mono - self._last_result_republish >= 1.0:
            for event in self.ledger.unacked_results():
                self._publish_event(event)
            self._last_result_republish = now_mono
        record = self.active or self.pending
        if record:
            age = (now_mono - self.heartbeat_received_mono
                   if self.heartbeat_received_mono is not None else float("inf"))
            reason = active_safety_abort_reason(record, self.heartbeat, age, now_mono)
            if reason:
                self._abort_active(reason, unknown=True)
                return
        if self.active and self.active.get("timeout_mono") is not None and now_mono >= self.active["timeout_mono"]:
            self._abort_active("PickPlace action timed out before result reporting margin", unknown=True)
            return
        if self.pending:
            self._progress_preflight(self.pending)
        if self.active:
            self._progress_action(self.active)
        if not self.active and not self.pending and self.state not in ("fault", "busy"):
            self.state = "ready" if self._heartbeat_fresh() and self.ledger.pending_result_count() == 0 else "initializing"
            if not self.heartbeat_problem and not self._heartbeat_fresh():
                self.detail = "waiting for recent server heartbeat"

    def _progress_preflight(self, record):
        if not self.client.server_is_ready():
            self._complete(False, "PickPlace action server /pick_place is unavailable")
            return
        if record["phase"] == "preflight":
            if not self.status_client.service_is_ready():
                self._complete(False, "/mirobot/status service is unavailable")
                return
            record["service_future"] = self.status_client.call_async(Trigger.Request())
            record["phase"] = "status_response"
            return
        future = record.get("service_future")
        if not future or not future.done():
            return
        try:
            response = future.result()
        except Exception as exc:
            self._complete(False, f"/mirobot/status request failed: {type(exc).__name__}")
            return
        expected = self.mode
        if not response.success or f"mode={expected}" not in response.message:
            self._complete(False, f"Mirobot not ready or controller mode mismatch: {response.message[:400]}")
            return
        if self.mode == "simulation" and not self.simulation_action_backend:
            self._complete(True, "Simulation stub completed; no ROS action, serial, or robot controller was called.")
            return
        try:
            self._send_goal(record)
        except Exception as exc:
            self._complete(False, f"Could not send PickPlace goal: {type(exc).__name__}: {str(exc)[:300]}")

    def _progress_action(self, record):
        if record["phase"] == "goal_response":
            future = record["goal_future"]
            if not future.done():
                return
            try:
                handle = future.result()
            except Exception as exc:
                self._complete(False, f"Action goal response failed: {type(exc).__name__}")
                return
            if not handle.accepted:
                self._complete(False, "PickPlace action goal was rejected")
                return
            record["goal_handle"] = handle
            record["result_future"] = handle.get_result_async()
            record["phase"] = "action_result"
        future = record.get("result_future")
        if not future or not future.done():
            return
        try:
            wrapped = future.result()
            result = wrapped.result
            success = action_result_success(wrapped.status, result.success)
            detail = result.message if result.message else ("PickPlace action succeeded" if success else f"PickPlace action ended with status {wrapped.status}")
            if not success:
                detail = f"PickPlace action failed (status={wrapped.status}, success={result.success}): {detail}"
            self._complete(success, detail)
        except Exception as exc:
            self._abort_active(f"PickPlace result unresolved: {type(exc).__name__}", unknown=True)

    def _publish_event(self, event):
        message = String()
        message.data = canonical_json(event)
        self.pub_events.publish(message)

    def _publish_status(self):
        status = {
            "v": 1, "device_id": "mirobot_laptop", "device": "robot",
            "session_id": self.session_id, "mode": self.mode,
            "state": self.state, "active_command_id": (
                (self.active or self.pending)["command"]["command_id"] if (self.active or self.pending) else None
            ), "detail": str(self.detail)[:500], "sent_at": time.time(),
        }
        message = String()
        message.data = canonical_json(status)
        self.pub_status.publish(message)

    def destroy_node(self):
        try:
            if self.active or self.pending:
                self._abort_active("adapter shutdown while command active; outcome unconfirmed", unknown=True)
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
