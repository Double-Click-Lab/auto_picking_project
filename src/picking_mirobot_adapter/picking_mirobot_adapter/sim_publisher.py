"""One-shot protocol v1 simulation command publisher for server bring-up."""

import argparse
import math
import time
import uuid

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from .adapter_node import TOPICS
from .protocol import canonical_json


def main(args=None):
    parser = argparse.ArgumentParser(description="Publish one simulation-only Mirobot command")
    parser.add_argument("--server-session-id", required=True, help="current server process UUID")
    parser.add_argument("--item-id", default="simulation-item")
    parser.add_argument("--bin", default="A")
    parser.add_argument("--lifetime-sec", type=float, default=60.0)
    options, ros_args = parser.parse_known_args(args)
    try:
        server_session = str(uuid.UUID(options.server_session_id))
    except ValueError as exc:
        parser.error(f"invalid --server-session-id: {exc}")
    if not math.isfinite(options.lifetime_sec) or options.lifetime_sec <= 3:
        parser.error("--lifetime-sec must be finite and exceed 3 seconds")
    rclpy.init(args=ros_args)
    node = Node("picking_sim_test_publisher")
    qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=10,
                     reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.VOLATILE)
    publisher = node.create_publisher(String, TOPICS["command"], qos)
    command = {
        "v": 1, "server_session_id": server_session,
        "command_id": str(uuid.uuid4()), "order_id": str(uuid.uuid4()),
        "device": "robot", "action": "pick_and_place", "mode": "simulation",
        "issued_at": time.time(), "expires_at": time.time() + options.lifetime_sec,
        "payload": {"item_id": options.item_id, "bin": options.bin,
                    "quantity": 1, "destination_id": ""},
    }
    command["payload"]["destination_id"] = command["order_id"]
    message = String()
    message.data = canonical_json(command)
    try:
        deadline = time.monotonic() + 10.0
        while rclpy.ok() and publisher.get_subscription_count() == 0 and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
        if publisher.get_subscription_count() == 0:
            raise RuntimeError("no /picking/robot/command subscriber found")
        publisher.publish(message)
        node.get_logger().info(f"Published simulation command {command['command_id']} (no hardware command is sent by this publisher)")
        rclpy.spin_once(node, timeout_sec=0.2)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
