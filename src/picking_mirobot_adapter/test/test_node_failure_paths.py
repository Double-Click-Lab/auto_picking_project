import uuid

from picking_mirobot_adapter.adapter_node import MirobotTopicNode
from picking_mirobot_adapter.ledger import Ledger


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message.data)


class FakeGoalHandle:
    def __init__(self):
        self.cancel_requested = False

    def cancel_goal_async(self):
        self.cancel_requested = True
        return object()


def command():
    order = str(uuid.uuid4())
    return {"v": 1, "server_session_id": str(uuid.uuid4()),
            "command_id": str(uuid.uuid4()), "order_id": order,
            "device": "robot", "action": "pick_and_place", "mode": "simulation",
            "issued_at": 1.0, "expires_at": 100.0,
            "payload": {"item_id": "item", "bin": "A", "quantity": 1,
                        "destination_id": order}}


def configured_node(tmp_path, cmd, goal_handle=None):
    node = object.__new__(MirobotTopicNode)
    node.ledger = Ledger(str(tmp_path / f"{cmd['command_id']}.db"))
    node.ledger.get_or_create(cmd)
    node.ledger.mark_running(cmd["command_id"])
    node.pub_events = FakePublisher()
    node.active = {"command": cmd, "goal_handle": goal_handle}
    node.pending = None
    node.heartbeat = None
    node.heartbeat_received_mono = None
    node.state = "busy"
    node.detail = "running"
    return node


def test_cancel_request_records_failure_and_does_not_claim_stop(tmp_path):
    cmd = command()
    handle = FakeGoalHandle()
    node = configured_node(tmp_path, cmd, handle)
    node._abort_active("server cancel", unknown=True)
    row = node.ledger.command_row(cmd["command_id"])
    assert handle.cancel_requested
    assert row["result"]["success"] is False
    assert "unconfirmed" in row["result"]["detail"]
    assert node.state == "fault" and node.ledger.get_fault()
    node.ledger.close()


def test_action_rejection_failure_is_reported_false(tmp_path):
    cmd = command()
    node = configured_node(tmp_path, cmd)
    node._complete(False, "PickPlace action goal was rejected")
    row = node.ledger.command_row(cmd["command_id"])
    assert row["result"]["success"] is False
    assert "rejected" in row["result"]["detail"]
    node.ledger.close()


def test_timeout_fault_is_latched_without_asserting_physical_stop(tmp_path):
    cmd = command()
    handle = FakeGoalHandle()
    node = configured_node(tmp_path, cmd, handle)
    node._abort_active("PickPlace action timed out", unknown=True)
    row = node.ledger.command_row(cmd["command_id"])
    assert handle.cancel_requested
    assert row["result"]["success"] is False
    assert "physical stop is unconfirmed" in row["result"]["detail"]
    assert node.ledger.get_fault()
    node.ledger.close()
