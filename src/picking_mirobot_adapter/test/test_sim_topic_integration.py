"""Opt-in DDS integration test of the fixed-plan software simulation stub."""

import json
import os
import subprocess
import time
import uuid

import pytest


@pytest.mark.skipif(os.environ.get("MIROBOT_RUN_SIM_INTEGRATION") != "1",
                    reason="set MIROBOT_RUN_SIM_INTEGRATION=1 to start ROS simulation processes")
def test_topic_command_runs_fixed_plan_stub_and_acks_result(tmp_path):
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String

    env = dict(os.environ, ROS_DOMAIN_ID="7", ROS_LOCALHOST_ONLY="0")
    adapter_log = open(tmp_path / "adapter.log", "w", encoding="utf-8")
    adapter = None
    node = None
    try:
        time.sleep(2)
        db = str(tmp_path / "adapter.sqlite3")
        adapter = subprocess.Popen([
            "ros2", "run", "picking_mirobot_adapter", "mirobot_topic_node", "--ros-args",
            "-p", "mode:=simulation",
            "-p", f"database_path:={db}",
        ], env=env, stdout=adapter_log, stderr=subprocess.STDOUT)
        rclpy.init()
        node = Node("mirobot_sim_integration_test")
        qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=10,
                         reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.VOLATILE)
        heartbeat_pub = node.create_publisher(String, "/picking/server_heartbeat", qos)
        command_pub = node.create_publisher(String, "/picking/robot/command", qos)
        ack_pub = node.create_publisher(String, "/picking/result_ack", qos)
        received = []
        node.create_subscription(String, "/picking/device_events", lambda msg: received.append(json.loads(msg.data)), qos)
        session_id = str(uuid.uuid4())
        order_id = str(uuid.uuid4())
        command_id = str(uuid.uuid4())
        sent_command = False
        final = None
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline and final is None:
            now = time.time()
            heartbeat_pub.publish(String(data=json.dumps({"v": 1, "server_session_id": session_id, "sent_at": now})))
            if (not sent_command and command_pub.get_subscription_count() > 0
                    and heartbeat_pub.get_subscription_count() > 0):
                command = {
                    "v": 1, "server_session_id": session_id,
                    "command_id": command_id, "order_id": order_id,
                    "device": "robot", "action": "pick_and_place", "mode": "simulation",
                    "issued_at": now, "expires_at": now + 45,
                    "payload": {"item_id": "integration", "bin": "A", "quantity": 1,
                                "destination_id": order_id},
                }
                command_pub.publish(String(data=json.dumps(command)))
                sent_command = True
            for event in received:
                if event["command_id"] == command_id and event["phase"] == "result":
                    final = event
                    ack_pub.publish(String(data=json.dumps({
                        "v": 1, "server_session_id": session_id,
                        "command_id": command_id, "device": "robot",
                        "event_id": event["event_id"], "accepted": True,
                    })))
                    break
            rclpy.spin_once(node, timeout_sec=0.1)
        assert sent_command, "adapter never subscribed to command topic"
        assert final is not None, "no terminal event before timeout; inspect child logs"
        assert final["success"] is True, final
        assert final["device"] == "robot" and final["mode"] == "simulation"
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        for process in (adapter,):
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        adapter_log.close()

