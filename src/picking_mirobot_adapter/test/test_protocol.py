import copy
import time
import uuid

import pytest

from picking_mirobot_adapter.protocol import (
    ProtocolError, parse_json_object, validate_ack, validate_command,
    validate_heartbeat,
)
from picking_mirobot_adapter.adapter_node import (
    cancel_matches, command_rejection_reason,
)


def command(mode="simulation", *, expires=None):
    order = str(uuid.uuid4())
    return {
        "v": 1, "server_session_id": str(uuid.uuid4()),
        "command_id": str(uuid.uuid4()), "order_id": order,
        "device": "robot", "action": "pick_and_place", "mode": mode,
        "issued_at": time.time(), "expires_at": expires or time.time() + 30,
        "payload": {"item_id": "item", "bin": "A", "quantity": 1,
                    "destination_id": order},
    }


def test_valid_command_and_quantity_exactly_one():
    assert validate_command(command())["payload"]["quantity"] == 1
    for invalid in (True, 1.0, 2):
        obj = command()
        obj["payload"]["quantity"] = invalid
        with pytest.raises(ProtocolError):
            validate_command(obj)


@pytest.mark.parametrize("raw", ["{", "[]", '{"a":1,"a":2}', '{"x":NaN}'])
def test_invalid_json_is_rejected(raw):
    with pytest.raises(ProtocolError):
        parse_json_object(raw)


def test_reject_wrong_destination_device_and_nonfinite_timestamp():
    obj = command()
    obj["payload"]["destination_id"] = str(uuid.uuid4())
    with pytest.raises(ProtocolError):
        validate_command(obj)
    obj = command()
    obj["expires_at"] = 10 ** 1000
    with pytest.raises(ProtocolError):
        validate_command(obj)
    obj = command()
    obj["device"] = "camera"
    with pytest.raises(ProtocolError):
        validate_command(obj)
    obj = command()
    obj["expires_at"] = float("inf")
    with pytest.raises(ProtocolError):
        validate_command(obj)


def test_heartbeat_and_exact_ack_validation():
    session, cid, eid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    assert validate_heartbeat({"v": 1, "server_session_id": session, "sent_at": time.time()})
    assert validate_ack({"v": 1, "server_session_id": session, "command_id": cid,
                         "device": "robot", "event_id": eid, "accepted": True})
    with pytest.raises(ProtocolError):
        validate_ack({"v": 1, "server_session_id": session, "command_id": cid,
                      "device": "robot", "event_id": eid, "accepted": 1})


def test_admission_rejects_mode_skew_expiry_session_and_pending_result():
    cmd = command()
    hb = {"server_session_id": cmd["server_session_id"], "sent_at": time.time()}
    now = hb["sent_at"]
    args = (cmd, "simulation", hb, 0.1, False, False, 0, now)
    assert command_rejection_reason(*args) == ""
    assert "mode" in command_rejection_reason(cmd, "real", hb, 0.1, False, False, 0, now)
    skewed = dict(hb, sent_at=now - 1.1)
    assert "clock" in command_rejection_reason(cmd, "simulation", skewed, 0.1, False, False, 0, now)
    assert "heartbeat" in command_rejection_reason(cmd, "simulation", hb, 5.1, False, False, 0, now)
    assert "session" in command_rejection_reason(cmd, "simulation", dict(hb, server_session_id=str(uuid.uuid4())), 0, False, False, 0, now)
    expiring = copy.deepcopy(cmd)
    expiring["expires_at"] = now + 2
    assert "2 seconds" in command_rejection_reason(expiring, "simulation", hb, 0, False, False, 0, now)
    assert "awaiting" in command_rejection_reason(cmd, "simulation", hb, 0, False, False, 1, now)


def test_cancel_requires_command_server_session_and_device_match():
    cmd = command()
    record = {"command": cmd}
    cancel = {"command_id": cmd["command_id"],
              "server_session_id": cmd["server_session_id"], "device": "robot"}
    assert cancel_matches(cancel, record)
    assert not cancel_matches(dict(cancel, command_id=str(uuid.uuid4())), record)
    assert not cancel_matches(dict(cancel, server_session_id=str(uuid.uuid4())), record)
    assert not cancel_matches(dict(cancel, device="camera"), record)
    assert not cancel_matches(cancel, None)

