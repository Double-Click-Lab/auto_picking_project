import threading
import uuid

from picking_mirobot_adapter.adapter_node import MirobotTopicNode
from picking_mirobot_adapter.ledger import Ledger


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message.data)


class FakeLogger:
    def error(self, _message):
        pass


def command():
    order = str(uuid.uuid4())
    return {"v": 1, "server_session_id": str(uuid.uuid4()),
            "command_id": str(uuid.uuid4()), "order_id": order,
            "device": "robot", "action": "pick_and_place", "mode": "real",
            "issued_at": 1.0, "expires_at": 100.0,
            "payload": {"item_id": "item", "bin": "A", "quantity": 1,
                        "destination_id": order}}


def configured_node(tmp_path, cmd):
    node = object.__new__(MirobotTopicNode)
    node.ledger = Ledger(str(tmp_path / f"{cmd['command_id']}.db"))
    node.ledger.get_or_create(cmd)
    node.ledger.mark_running(cmd["command_id"])
    node.pub_events = FakePublisher()
    node.mode = "real"
    node.get_logger = lambda: FakeLogger()
    node.active = {"command": cmd, "cancel_event": threading.Event(),
                   "terminal_recorded": False, "worker": None}
    node.state = "busy"
    node.detail = "running"
    return node


def test_cancel_records_failure_and_does_not_claim_physical_stop(tmp_path):
    cmd = command()
    node = configured_node(tmp_path, cmd)
    node._request_abort("server cancel: test")
    row = node.ledger.command_row(cmd["command_id"])
    assert node.active["cancel_event"].is_set()
    assert row["result"]["success"] is False
    assert "physical stop is unconfirmed" in row["result"]["detail"]
    assert "may continue" in row["result"]["detail"]
    assert node.state == "fault" and node.ledger.get_fault()
    assert len(node.pub_events.messages) == 1
    node.ledger.close()


def test_repeated_abort_preserves_original_result_event(tmp_path):
    cmd = command()
    node = configured_node(tmp_path, cmd)
    node._request_abort("heartbeat lost")
    original = node.ledger.command_row(cmd["command_id"])["result"]
    node._request_abort("second reason")
    current = node.ledger.command_row(cmd["command_id"])["result"]
    assert current == original
    assert len(node.pub_events.messages) == 1
    node.ledger.close()
