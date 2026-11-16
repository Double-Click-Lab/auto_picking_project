"""Strict validation and canonical JSON helpers for protocol v1."""

import json
import math
import uuid


class ProtocolError(ValueError):
    pass


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ProtocolError(f"non-finite JSON number: {value}")


def parse_json_object(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_pairs_no_duplicates,
                           parse_constant=_reject_constant)
    except (json.JSONDecodeError, UnicodeError, TypeError, RecursionError,
            OverflowError) as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("JSON root must be an object")
    return value


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _exact_fields(obj, fields, label):
    if set(obj) != set(fields):
        missing = sorted(set(fields) - set(obj))
        extra = sorted(set(obj) - set(fields))
        raise ProtocolError(f"{label} fields mismatch (missing={missing}, extra={extra})")


def _uuid(value, field):
    if not isinstance(value, str):
        raise ProtocolError(f"{field} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ProtocolError(f"{field} must be a UUID string") from exc
    if str(parsed) != value:
        raise ProtocolError(f"{field} must use canonical UUID form")


def _finite_number(value, name, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{name} must be a JSON number")
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError):
        finite = False
    if not finite:
        raise ProtocolError(f"{name} must be finite")
    if positive and value <= 0:
        raise ProtocolError(f"{name} must be positive")
    if nonnegative and value < 0:
        raise ProtocolError(f"{name} must be nonnegative")


def validate_command(obj):
    fields = ("v", "server_session_id", "command_id", "order_id", "device",
              "action", "mode", "issued_at", "expires_at", "payload")
    _exact_fields(obj, fields, "command")
    if type(obj["v"]) is not int or obj["v"] != 1:
        raise ProtocolError("v must be integer 1")
    for key in ("server_session_id", "command_id", "order_id"):
        _uuid(obj[key], key)
    if obj["device"] != "robot" or obj["action"] != "pick_and_place":
        raise ProtocolError("robot topic only accepts device=robot/action=pick_and_place")
    if obj["mode"] not in ("simulation", "real"):
        raise ProtocolError("mode must be simulation or real")
    _finite_number(obj["issued_at"], "issued_at")
    _finite_number(obj["expires_at"], "expires_at")
    if obj["expires_at"] <= obj["issued_at"]:
        raise ProtocolError("expires_at must be later than issued_at")
    payload = obj["payload"]
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be an object")
    _exact_fields(payload, ("item_id", "bin", "quantity", "destination_id"), "robot payload")
    for key in ("item_id", "bin", "destination_id"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise ProtocolError(f"payload.{key} must be a nonempty string")
    if type(payload["quantity"]) is not int or payload["quantity"] != 1:
        raise ProtocolError("payload.quantity must be exactly integer 1")
    if payload["destination_id"] != obj["order_id"]:
        raise ProtocolError("payload.destination_id must equal order_id")
    return obj


def validate_heartbeat(obj):
    _exact_fields(obj, ("v", "server_session_id", "sent_at"), "heartbeat")
    if type(obj["v"]) is not int or obj["v"] != 1:
        raise ProtocolError("v must be integer 1")
    _uuid(obj["server_session_id"], "server_session_id")
    _finite_number(obj["sent_at"], "sent_at")
    return obj


def validate_ack(obj):
    _exact_fields(obj, ("v", "server_session_id", "command_id", "device",
                        "event_id", "accepted"), "result_ack")
    if type(obj["v"]) is not int or obj["v"] != 1:
        raise ProtocolError("v must be integer 1")
    for key in ("server_session_id", "command_id", "event_id"):
        _uuid(obj[key], key)
    if obj["device"] != "robot" or obj["accepted"] is not True:
        raise ProtocolError("ack must identify robot and set accepted=true")
    return obj


def validate_cancel(obj):
    _exact_fields(obj, ("v", "server_session_id", "command_id", "device", "reason"), "cancel")
    if type(obj["v"]) is not int or obj["v"] != 1:
        raise ProtocolError("v must be integer 1")
    _uuid(obj["server_session_id"], "server_session_id")
    _uuid(obj["command_id"], "command_id")
    if obj["device"] != "robot" or not isinstance(obj["reason"], str):
        raise ProtocolError("cancel device must be robot and reason a string")
    return obj

