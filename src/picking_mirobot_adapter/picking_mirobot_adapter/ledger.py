"""Durable command deduplication and result outbox."""

import json
import sqlite3
import threading
import time
import uuid

from .protocol import canonical_json


class Ledger:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS commands (
            command_id TEXT PRIMARY KEY, canonical TEXT NOT NULL,
            command_json TEXT NOT NULL, server_session_id TEXT NOT NULL,
            order_id TEXT NOT NULL, accepted_event TEXT NOT NULL,
            result_event TEXT, state TEXT NOT NULL, acked INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS protocol_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT, command_id TEXT, detail TEXT NOT NULL,
            received_json TEXT NOT NULL, created_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.commit()

    @staticmethod
    def _event(command, phase, success=None, detail="", event_id=None):
        return {
            "v": 1,
            "server_session_id": command["server_session_id"],
            "event_id": event_id or str(uuid.uuid4()),
            "command_id": command["command_id"],
            "order_id": command["order_id"],
            "device": "robot",
            "mode": command["mode"],
            "phase": phase,
            "success": success,
            "detail": str(detail)[:500],
        }

    def get_or_create(self, command):
        command_id = command["command_id"]
        canonical = canonical_json(command)
        with self.lock, self.db:
            row = self.db.execute("SELECT canonical, accepted_event, result_event FROM commands WHERE command_id=?", (command_id,)).fetchone()
            if row:
                if row[0] != canonical:
                    self.db.execute("INSERT INTO protocol_errors(command_id,detail,received_json,created_at) VALUES(?,?,?,?)",
                                    (command_id, "same command_id received with changed content", canonical, time.time()))
                    return "conflict", None, None
                return "duplicate", json.loads(row[1]), json.loads(row[2]) if row[2] else None
            accepted = self._event(command, "accepted", None, "Command accepted for processing")
            self.db.execute("INSERT INTO commands(command_id,canonical,command_json,server_session_id,order_id,accepted_event,state,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            (command_id, canonical, canonical, command["server_session_id"], command["order_id"], canonical_json(accepted), "accepted", time.time()))
            return "new", accepted, None

    def pending_result_count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM commands WHERE result_event IS NOT NULL AND acked=0").fetchone()[0]

    def command_row(self, command_id):
        with self.lock:
            row = self.db.execute("SELECT command_json,state,result_event,acked FROM commands WHERE command_id=?", (command_id,)).fetchone()
        return None if row is None else {"command": json.loads(row[0]), "state": row[1], "result": json.loads(row[2]) if row[2] else None, "acked": bool(row[3])}

    def mark_running(self, command_id):
        with self.lock, self.db:
            self.db.execute("UPDATE commands SET state='running' WHERE command_id=? AND result_event IS NULL", (command_id,))

    def finish(self, command, success, detail, *, unknown=False):
        event = self._event(command, "result", bool(success), detail)
        with self.lock, self.db:
            row = self.db.execute("SELECT result_event FROM commands WHERE command_id=?", (command["command_id"],)).fetchone()
            if row is None:
                raise RuntimeError("cannot finish an unrecorded command")
            if row[0]:
                return json.loads(row[0])
            self.db.execute("UPDATE commands SET result_event=?,state=? WHERE command_id=?",
                            (canonical_json(event), "unknown" if unknown else "result", command["command_id"]))
            if unknown:
                self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('fault_latched','1')")
            return event

    def fail_unfinished_on_restart(self):
        with self.lock:
            rows = self.db.execute("SELECT command_json FROM commands WHERE result_event IS NULL").fetchall()
        for (raw,) in rows:
            command = json.loads(raw)
            self.finish(command, False, "Process restarted while command outcome was unresolved; not retried. Operator inspection required.", unknown=True)

    def unacked_results(self):
        with self.lock:
            rows = self.db.execute("SELECT result_event FROM commands WHERE result_event IS NOT NULL AND acked=0").fetchall()
        return [json.loads(row[0]) for row in rows]

    def acknowledge(self, ack):
        with self.lock, self.db:
            row = self.db.execute("SELECT server_session_id,result_event FROM commands WHERE command_id=?", (ack["command_id"],)).fetchone()
            if row is None or not row[1]:
                return False
            event = json.loads(row[1])
            if (row[0] != ack["server_session_id"] or event["event_id"] != ack["event_id"]
                    or event["device"] != ack["device"] or ack["accepted"] is not True):
                return False
            self.db.execute("UPDATE commands SET acked=1 WHERE command_id=?", (ack["command_id"],))
            return True

    def get_fault(self):
        with self.lock:
            row = self.db.execute("SELECT value FROM metadata WHERE key='fault_latched'").fetchone()
        return bool(row and row[0] == "1")

    def latch_fault(self):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('fault_latched','1')")

    def clear_fault_after_inspection(self):
        with self.lock, self.db:
            unresolved = self.db.execute("SELECT COUNT(*) FROM commands WHERE state='running'").fetchone()[0]
            pending = self.db.execute("SELECT COUNT(*) FROM commands WHERE result_event IS NOT NULL AND acked=0").fetchone()[0]
            if unresolved or pending:
                raise RuntimeError("cannot clear fault while commands are unresolved or results are unacknowledged")
            self.db.execute("UPDATE commands SET state='inspected' WHERE state='unknown'")
            self.db.execute("DELETE FROM metadata WHERE key='fault_latched'")

    def close(self):
        with self.lock:
            self.db.close()
