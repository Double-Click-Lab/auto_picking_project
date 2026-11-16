import json
import uuid

from picking_mirobot_adapter.ledger import Ledger


def command():
    order = str(uuid.uuid4())
    return {"v": 1, "server_session_id": str(uuid.uuid4()),
            "command_id": str(uuid.uuid4()), "order_id": order,
            "device": "robot", "action": "pick_and_place", "mode": "simulation",
            "issued_at": 1.0, "expires_at": 100.0,
            "payload": {"item_id": "x", "bin": "A", "quantity": 1,
                        "destination_id": order}}


def test_duplicate_never_creates_a_second_execution_and_changed_content_is_logged(tmp_path):
    ledger = Ledger(str(tmp_path / "state.db"))
    doc = command()
    first = ledger.get_or_create(doc)
    duplicate = ledger.get_or_create(doc)
    assert first[0] == "new"
    assert duplicate[0] == "duplicate"
    changed = json.loads(json.dumps(doc))
    changed["payload"]["item_id"] = "different"
    assert ledger.get_or_create(changed)[0] == "conflict"
    assert ledger.command_row(doc["command_id"])["command"]["payload"]["item_id"] == "x"
    assert ledger.db.execute("SELECT COUNT(*) FROM protocol_errors").fetchone()[0] == 1
    ledger.close()


def test_each_unit_quantity_command_uses_a_new_command_id(tmp_path):
    ledger = Ledger(str(tmp_path / "state.db"))
    first, second = command(), command()
    assert first["command_id"] != second["command_id"]
    assert ledger.get_or_create(first)[0] == "new"
    assert ledger.get_or_create(second)[0] == "new"
    assert ledger.db.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 2
    ledger.close()


def test_result_outbox_retries_until_exact_ack(tmp_path):
    ledger = Ledger(str(tmp_path / "state.db"))
    cmd = command()
    _, accepted, _ = ledger.get_or_create(cmd)
    event = ledger.finish(cmd, False, "cancel requested; stop unconfirmed")
    assert event["phase"] == "result" and event["success"] is False
    assert ledger.unacked_results() == [event]
    bad = {"server_session_id": cmd["server_session_id"], "command_id": cmd["command_id"],
           "device": "robot", "event_id": str(uuid.uuid4()), "accepted": True}
    assert not ledger.acknowledge(bad)
    assert ledger.pending_result_count() == 1
    good = dict(bad, event_id=event["event_id"])
    assert ledger.acknowledge(good)
    assert ledger.unacked_results() == []
    assert accepted["success"] is None
    ledger.close()


def test_restart_marks_inflight_unknown_and_never_retries(tmp_path):
    path = str(tmp_path / "state.db")
    ledger = Ledger(path)
    cmd = command()
    ledger.get_or_create(cmd)
    ledger.mark_running(cmd["command_id"])
    ledger.close()
    restored = Ledger(path)
    restored.fail_unfinished_on_restart()
    row = restored.command_row(cmd["command_id"])
    assert row["state"] == "unknown"
    assert row["result"]["success"] is False
    assert "not retried" in row["result"]["detail"]
    assert restored.get_fault()
    assert restored.get_or_create(cmd)[0] == "duplicate"
    restored.close()


def test_fault_recovery_requires_acks_and_explicit_inspection(tmp_path):
    ledger = Ledger(str(tmp_path / "state.db"))
    cmd = command()
    ledger.get_or_create(cmd)
    ledger.finish(cmd, False, "unknown", unknown=True)
    try:
        ledger.clear_fault_after_inspection()
        assert False, "unacknowledged result must block recovery"
    except RuntimeError:
        pass
    event = ledger.unacked_results()[0]
    ledger.acknowledge({"server_session_id": event["server_session_id"],
                        "command_id": event["command_id"], "device": "robot",
                        "event_id": event["event_id"], "accepted": True})
    ledger.clear_fault_after_inspection()
    assert not ledger.get_fault()
    assert ledger.command_row(cmd["command_id"])["state"] == "inspected"
    ledger.close()

