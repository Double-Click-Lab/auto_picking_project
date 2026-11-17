"""ROS independent durable protocol engine. All methods run on one owner thread."""
import fcntl
import json
import math
import sqlite3
import time
import uuid
from pathlib import Path


def uid(value):
    if not isinstance(value, str):
        raise ValueError('UUID string required')
    uuid.UUID(value)
    return value


def number(x):
    return type(x) in (int, float) and math.isfinite(x)


def decode(raw):
    obj = json.loads(raw, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    if not isinstance(obj, dict) or type(obj.get('v')) is not int or obj['v'] != 1:
        raise ValueError('v=1 JSON object required')
    return obj


class Engine:
    def __init__(self, path, backend, emit, mapping, simulation=True, wall=time.time, mono=time.monotonic):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(str(path) + '.lock', 'a+')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self.lock.close()
            raise
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, body TEXT, accepted TEXT, result TEXT, ack INTEGER DEFAULT 0)')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS errors (at REAL, detail TEXT)')
        self.backend, self.emit, self.mapping = backend, emit, mapping
        self.mode = 'simulation' if simulation else 'real'
        self.wall, self.mono = wall, mono
        self.session = str(uuid.uuid4())
        self.hb = None
        self.active = None
        self.last_motor = None
        self.detail = ''
        self.fault = bool(self.db.execute("SELECT 1 FROM meta WHERE key='fault'").fetchone())
        for row in self.db.execute('SELECT body FROM commands WHERE result IS NULL').fetchall():
            self.fault = True
            self.finish(json.loads(row[0]), False, 'restart: outcome unknown; operator recovery required')
        if self.fault:
            self.latch('operator recovery required')
        self.last_publish = -1e10

    def latch(self, detail):
        self.fault, self.detail = True, detail
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('fault', ?)", (detail,))
        self.db.commit()

    def error(self, detail):
        self.detail = str(detail)[:500]
        self.db.execute('INSERT INTO errors VALUES (?,?)', (self.wall(), self.detail))
        self.db.commit()

    def event(self, c, phase, success, detail=''):
        return dict(v=1, server_session_id=c['server_session_id'], event_id=str(uuid.uuid4()),
                    command_id=c['command_id'], order_id=c['order_id'], device=c['device'],
                    mode=c['mode'], phase=phase, success=success, detail=detail[:500])

    def finish(self, c, success, detail):
        e = self.event(c, 'result', success, detail)
        self.db.execute('UPDATE commands SET result=? WHERE id=? AND result IS NULL',
                        (json.dumps(e, ensure_ascii=False), c['command_id']))
        self.db.commit()
        self.emit(e)

    def validate(self, c, device):
        for k in ('server_session_id', 'command_id', 'order_id'):
            uid(c.get(k))
        if c.get('device') != device or device not in ('motor', 'camera'):
            raise ValueError('wrong device/topic')
        if c.get('action') != {'motor':'move_to_bin', 'camera':'align_bin'}[device]:
            raise ValueError('wrong action')
        if c.get('mode') not in ('simulation', 'real'):
            raise ValueError('invalid mode')
        for k in ('issued_at', 'expires_at'):
            if not number(c.get(k)):
                raise ValueError('invalid timestamp')
        if c['issued_at'] >= c['expires_at']:
            raise ValueError('timestamp order')
        p = c.get('payload')
        if not isinstance(p, dict):
            raise ValueError('payload object required')
        for k in ('item_id','bin','destination_id'):
            if not isinstance(p.get(k), str) or not p[k]:
                raise ValueError('payload string required')
        if p['destination_id'] != c['order_id'] or type(p.get('quantity')) is not int or p['quantity'] != 1:
            raise ValueError('destination/quantity mismatch')
        if type(p.get('marker_id')) is not int or not 1 <= p['marker_id'] <= 4:
            raise ValueError('marker_id must be integer 1..4')
        if not number(p.get('position_mm')) or not number(p.get('motor_length_mm')) or not 0 <= p['position_mm'] <= p['motor_length_mm'] or p['motor_length_mm'] <= 0:
            raise ValueError('invalid mm bounds')

    def receive(self, kind, raw):
        try:
            c = decode(raw)
            if kind == 'heartbeat':
                uid(c.get('server_session_id'))
                if not number(c.get('sent_at')):
                    raise ValueError('invalid heartbeat time')
                if self.active and c['server_session_id'] != self.active['server_session_id']:
                    self.abort('server session changed')
                self.hb = (c['server_session_id'], self.mono(), c['sent_at'] - self.wall())
            elif kind == 'ack':
                if c.get('accepted') is not True:
                    return
                for k in ('server_session_id', 'command_id', 'event_id'):
                    uid(c.get(k))
                if c.get('device') not in ('motor', 'camera'):
                    raise ValueError('invalid ack device')
                row = self.db.execute('SELECT result FROM commands WHERE id=?', (c.get('command_id'),)).fetchone()
                if row and row[0]:
                    e = json.loads(row[0])
                    if all(c.get(k) == e[k] for k in ('server_session_id','command_id','device','event_id')):
                        self.db.execute('UPDATE commands SET ack=1 WHERE id=?', (c['command_id'],))
                        self.db.commit()
            elif kind == 'cancel':
                if self.active and all(c.get(k) == self.active[k] for k in ('server_session_id','command_id','device')):
                    if not isinstance(c.get('reason'), str):
                        raise ValueError('cancel reason string required')
                    self.abort('cancel: ' + c['reason'])
            else:
                self.command(c, kind)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self.error('protocol: ' + str(exc))

    def command(self, c, device):
        self.validate(c, device)
        body = json.dumps(c, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        row = self.db.execute('SELECT body,accepted,result FROM commands WHERE id=?', (c['command_id'],)).fetchone()
        if row:
            if row[0] != body:
                self.error('protocol: same command_id with different content')
            else:
                self.emit(json.loads(row[2] or row[1]))
            return
        # Refusals are durable results too, with no accepted event.
        reason = None
        if c['mode'] != self.mode: reason = 'mode mismatch'
        elif self.fault: reason = 'operator recovery required'
        elif self.active or self.db.execute('SELECT 1 FROM commands WHERE result IS NOT NULL AND ack=0').fetchone(): reason = 'busy or unacknowledged result'
        elif not self.backend.ready: reason = 'local preparation required'
        elif not self.hb or self.hb[0] != c['server_session_id'] or self.mono()-self.hb[1] > 5: reason = 'matching recent heartbeat required'
        elif abs(self.hb[2]) > 1 or c['issued_at'] > self.wall()+1: reason = 'clock skew exceeds 1s'
        elif c['expires_at']-self.wall() <= 2: reason = 'expiry requires more than 2s'
        elif self.mapping.get(c['payload']['item_id'] + '/' + c['payload']['bin']) != c['payload']['marker_id']: reason = 'mapping mismatch'
        elif device == 'camera' and self.last_motor != self.identity(c): reason = 'same order motor success required'
        accepted = self.event(c, 'accepted', None)
        self.db.execute('INSERT INTO commands(id,body,accepted) VALUES (?,?,?)', (c['command_id'], body, json.dumps(accepted)))
        self.db.commit()  # before any hardware operation
        if reason:
            self.finish(c, False, reason)
            return
        self.active = c
        self.deadline = self.mono() + c['expires_at']-self.wall()-1
        self.emit(accepted)
        if device == 'motor': self.last_motor = None
        try:
            self.backend.start(c)
        except Exception as exc:
            self.abort('start error: ' + str(exc))

    @staticmethod
    def identity(c):
        p = c['payload']
        return (c['server_session_id'], c['order_id'], p['item_id'], p['bin'], p['marker_id'])

    def abort(self, reason):
        if not self.active: return
        self.last_motor = None
        try:
            stop = self.backend.stop()
        except Exception as exc:
            stop = 'STOP failed: '+str(exc)
        c, self.active = self.active, None
        self.latch(reason + '; ' + stop + '; physical stop not guaranteed')
        self.finish(c, False, self.detail)

    def tick(self):
        if self.active:
            if self.mono() >= self.deadline or self.wall() >= self.active['expires_at']:
                self.abort('command deadline')
            elif not self.hb or self.hb[0] != self.active['server_session_id'] or self.mono()-self.hb[1] > 5:
                self.abort('server heartbeat lost')
        try:
            result = self.backend.poll()
            if self.active and result is not None:
                if result:
                    c, self.active = self.active, None
                    if c['device'] == 'motor': self.last_motor = self.identity(c)
                    self.finish(c, True, self.backend.success_detail)
                else:
                    self.abort('alignment failed')
        except Exception as exc:
            if self.active: self.abort('hardware: ' + str(exc))
            else:
                # Local original homing can move without a server command.
                # A camera/serial error must stop that motion as well.
                try: stop = self.backend.stop()
                except Exception as stop_exc: stop = 'STOP failed: '+str(stop_exc)
                self.latch('hardware: '+str(exc)+'; '+str(stop))
        if self.mono()-self.last_publish >= 1:
            self.last_publish = self.mono()
            for (raw,) in self.db.execute('SELECT result FROM commands WHERE result IS NOT NULL AND ack=0'):
                self.emit(json.loads(raw))
            for device in ('motor','camera'):
                state = 'fault' if self.fault else 'busy' if self.active else 'ready' if self.backend.ready else 'initializing'
                self.emit(dict(v=1,device_id='camera_motor_laptop',device=device,session_id=self.session,
                               mode=self.mode,state=state,active_command_id=self.active['command_id'] if self.active else None,
                               detail=self.detail[:500],sent_at=self.wall()))

    def close(self):
        if self.active: self.abort('process shutdown')
        self.backend.close()
        self.db.close()
        self.lock.close()
