"""Durable HTTP <-> picking topic protocol v1 relay; no ROS imports here."""

import fcntl
import json
import math
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError
from uuid import UUID, uuid4

try:
    from .mirobot_bridge import WarehouseHTTP
except ImportError:
    from mirobot_bridge import WarehouseHTTP


ACTIONS = {'motor': 'move_to_bin', 'camera': 'align_bin', 'robot': 'pick_and_place'}


def number(value):
    return type(value) in (float, int) and math.isfinite(value)


def uid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('canonical UUID required')
    return value


def decode(raw):
    value = json.loads(raw, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    if not isinstance(value, dict) or type(value.get('v')) is not int or value['v'] != 1:
        raise ValueError('v=1 JSON object required')
    return value


def load_mapping(path):
    items = json.loads(Path(path).read_text())['items']
    if not isinstance(items, dict) or not items:
        raise ValueError('items mapping required')
    for name, marker in items.items():
        if not isinstance(name, str) or len(name.split('/')) != 2 or not all(name.split('/')):
            raise ValueError('mapping keys must be item_id/bin')
        if type(marker) is not int or marker not in range(1, 5):
            raise ValueError('marker_id must be integer 1..4')
    return items


class DeviceHTTP(WarehouseHTTP):
    def next_device(self, device):
        return self.request('GET', f'/devices/{device}/commands/next')['command']

    def complete_device(self, command, result):
        return self.request('POST', f'/devices/{command["device"]}/commands/'
                            f'{command["command_id"]}/complete', result)


class RelayJournal:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(str(path) + '.lock', 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError('A relay already owns this journal') from None
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS relay_commands '
                        '(id TEXT PRIMARY KEY, body TEXT NOT NULL, result TEXT, event TEXT, '
                        'posted INTEGER NOT NULL DEFAULT 0)')
        self.db.execute('CREATE TABLE IF NOT EXISTS relay_meta (key TEXT PRIMARY KEY, value TEXT)')
        # No old-session command is ever reissued after a relay process restart.
        rows = self.db.execute('SELECT id FROM relay_commands WHERE result IS NULL').fetchall()
        for row in rows:
            self.outcome(row['id'], {'success': False, 'detail':
                'Relay restarted during a command; physical outcome unknown; operator recovery required'})
            self.fault('Relay restarted with an unresolved command')

    def fault(self, detail):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO relay_meta VALUES ('fault', ?)", (detail[:500],))

    @property
    def fault_detail(self):
        row = self.db.execute("SELECT value FROM relay_meta WHERE key='fault'").fetchone()
        return row[0] if row else None

    def recover(self):
        if self.db.execute('SELECT 1 FROM relay_commands WHERE posted=0').fetchone():
            raise ValueError('Unreported commands remain; reconcile them before recovery')
        with self.db:
            self.db.execute("DELETE FROM relay_meta WHERE key='fault'")

    def record(self, command):
        with self.db:
            self.db.execute('INSERT INTO relay_commands (id,body) VALUES (?,?)',
                            (command['command_id'], json.dumps(command, sort_keys=True)))

    def get(self, command_id):
        return self.db.execute('SELECT * FROM relay_commands WHERE id=?', (command_id,)).fetchone()

    def outcome(self, command_id, result, event=None):
        with self.db:
            self.db.execute('UPDATE relay_commands SET result=?,event=? WHERE id=?',
                            (json.dumps(result, sort_keys=True),
                             json.dumps(event, sort_keys=True) if event else None, command_id))

    def close(self):
        self.db.close()
        self.lock.close()


class Relay:
    def __init__(self, http, journal, mapping, emit, mode='simulation',
                 wall=time.time, mono=time.monotonic):
        if mode not in ('simulation', 'real'):
            raise ValueError('invalid mode')
        self.http, self.journal, self.mapping, self.emit = http, journal, mapping, emit
        self.mode, self.wall, self.mono = mode, wall, mono
        self.session = str(uuid4())
        self.statuses = {}
        self.active = None
        self.health_at = float('-inf')
        self.health = None
        self.detail = 'Waiting for HTTP server and three devices'
        self.next_publish = 0
        self.settle_until = 0
        self.healthy_since = None

    def heartbeat(self):
        if self.mono() - self.health_at <= 2 and not self.journal.fault_detail:
            return dict(v=1, server_session_id=self.session, sent_at=self.wall())

    def status(self, raw):
        s = decode(raw)
        if s.get('device') not in ACTIONS or s.get('mode') not in ('simulation', 'real'):
            raise ValueError('invalid device status')
        uid(s.get('session_id'))
        if not number(s.get('sent_at')) or abs(s['sent_at']-self.wall()) > 1:
            raise ValueError('device clock skew exceeds 1s')
        expected = 'mirobot_laptop' if s['device'] == 'robot' else 'camera_motor_laptop'
        if s.get('device_id') != expected or s.get('state') not in ('initializing', 'ready', 'busy', 'fault'):
            raise ValueError('invalid device identity/state')
        active = s.get('active_command_id')
        if active is not None:
            uid(active)
        self.statuses[(s['device'], s['session_id'])] = (s, self.mono())

    def ready(self, devices=tuple(ACTIONS)):
        for device in devices:
            current = [(s, at) for (d, _), (s, at) in self.statuses.items()
                       if d == device and self.mono()-at <= 3]
            if len(current) != 1:
                return False
            s, at = current[0]
            if s['mode'] != self.mode or s['state'] != 'ready' or s['active_command_id'] is not None:
                return False
        return True

    def ack(self, event):
        self.emit('/picking/result_ack', dict(v=1, accepted=True, **{
            k: event[k] for k in ('server_session_id', 'command_id', 'device', 'event_id')}))

    def event(self, raw):
        e = decode(raw)
        for key in ('server_session_id', 'command_id', 'order_id', 'event_id'):
            uid(e.get(key))
        if not isinstance(e.get('detail'), str) or len(e['detail']) > 500:
            raise ValueError('invalid event detail')
        row = self.journal.get(e['command_id'])
        if row is None:
            raise ValueError('unknown command result')
        c = json.loads(row['body'])
        if any(e.get(k) != c[k] for k in ('server_session_id','order_id','device','mode')):
            raise ValueError('result does not match command')
        if e.get('phase') == 'accepted' and e.get('success') is None:
            return
        if e.get('phase') != 'result' or type(e.get('success')) is not bool:
            raise ValueError('invalid result')
        if row['result']:
            if row['event'] != json.dumps(e, sort_keys=True):
                self.journal.fault('Conflicting result; operator reconciliation required')
                raise ValueError('conflicting result')
            if row['posted']:
                self.ack(e)
                # Repeated results show that an ack may still be in flight.
                self.settle_until = self.mono()+1.2
            return
        outcome = {'success': e['success'], 'detail': e['detail']}
        if self.wall() >= c['expires_at']:
            outcome = {'success':False, 'detail':'Result arrived after command expiry; inspect physical state'}
        self.journal.outcome(c['command_id'], outcome, e)

    def cancel(self, command, reason):
        self.emit('/picking/cancel', dict(v=1, reason=reason[:500], **{
            k: command[k] for k in ('server_session_id','command_id','device')}))

    def fail_active(self, reason):
        if self.active:
            self.cancel(self.active, reason)
            row = self.journal.get(self.active['command_id'])
            if row['result'] is None:
                self.journal.outcome(self.active['command_id'], {'success': False, 'detail': reason[:500]})
            self.active = None
            self.journal.fault(reason)

    def report(self):
        for row in self.journal.db.execute('SELECT * FROM relay_commands WHERE posted=0 AND result IS NOT NULL').fetchall():
            c, result = json.loads(row['body']), json.loads(row['result'])
            try:
                response = self.http.complete_device(c, result)
                if response.get('accepted') is not True:
                    raise RuntimeError('HTTP server did not accept completion')
                posted = 1
            except HTTPError as exc:
                if exc.code not in (404, 409):
                    raise
                posted = 2
                self.journal.fault(f'HTTP {exc.code}: result rejected; inspect command {c["command_id"]}')
            with self.journal.db:
                self.journal.db.execute('UPDATE relay_commands SET posted=? WHERE id=?', (posted, row['id']))
            if row['event']:
                self.ack(json.loads(row['event']))
            if self.active and self.active['command_id'] == row['id']:
                self.active = None
            self.settle_until = self.mono()+1.2
            if not result['success']:
                self.journal.fault('Device failure: '+result['detail'])

    def make_command(self, source):
        device = source['device']
        uid(source['id']); uid(source['order_id'])
        if device not in ACTIONS or source['action'] != ACTIONS[device]:
            raise ValueError('unexpected HTTP action')
        payload = dict(source['payload'])
        if type(payload.get('quantity')) is not int or payload['quantity'] != 1 or payload.get('destination_id') != source['order_id']:
            raise ValueError('invalid quantity/destination')
        if not all(isinstance(payload.get(k), str) and payload[k] for k in ('item_id','bin')):
            raise ValueError('invalid item/bin')
        if device != 'robot':
            payload['marker_id'] = self.mapping[payload['item_id']+'/'+payload['bin']]
            if not number(payload.get('position_mm')) or not number(payload.get('motor_length_mm')) or not 0 <= payload['position_mm'] <= payload['motor_length_mm'] or payload['motor_length_mm'] <= 0:
                raise ValueError('invalid mm limits')
        else:
            # The Mirobot v1 adapter accepts exactly these four payload fields.
            payload = {key: payload[key] for key in ('item_id', 'bin', 'quantity', 'destination_id')}
        if not number(source.get('expires_at')) or source['expires_at']-self.wall() <= 2:
            raise ValueError('expired HTTP command')
        return dict(v=1, server_session_id=self.session, command_id=source['id'],
                    order_id=source['order_id'], device=device, action=source['action'], mode=self.mode,
                    issued_at=self.wall(), expires_at=source['expires_at'], payload=payload)

    def tick(self):
        self.report()  # replay saved outcomes after HTTP response loss; never repeat motion
        health = self.http.health()
        if health.get('device_transport') != 'topics' or health.get('mode') != self.mode or not health.get('worker_alive'):
            self.fail_active('HTTP mode/transport/worker mismatch')
            self.detail = 'Set DEVICE_TRANSPORT=topics and matching SIMULATION; worker must be alive'
            self.health_at = float('-inf')
            self.healthy_since = None
            return
        self.health, self.health_at = health, self.mono()
        if self.healthy_since is None:
            self.healthy_since = self.mono()
        if health.get('fault') or self.journal.fault_detail:
            self.fail_active('Server or relay fault; operator recovery required')
            self.detail = str(health.get('fault') or self.journal.fault_detail)
            return
        if self.active:
            c = self.active
            row = self.journal.get(c['command_id'])
            if row['result'] is not None:
                return
            current = [(s, at) for (d, _), (s, at) in self.statuses.items()
                       if d == c['device'] and self.mono()-at <= 5]
            if (len(current) != 1 or current[0][0]['mode'] != self.mode
                    or current[0][0]['state'] == 'fault'
                    or current[0][0]['active_command_id'] not in (None, c['command_id'])
                    or current[0][0]['session_id'] != self.active_device_session):
                self.fail_active('Device disappeared, restarted or entered fault; physical state unknown')
            elif self.mono() >= self.deadline or self.wall() >= c['expires_at']-1:
                self.fail_active('Device command expired; physical stop not guaranteed')
            elif self.mono() >= self.next_publish:
                self.emit('/picking/'+c['device']+'/command', c)
                self.next_publish = self.mono()+1
            return
        if self.mono() < max(self.settle_until, self.healthy_since+2) or not self.ready():
            self.detail = 'Waiting for ready motor/camera/robot, matching mode and recent status'
            return
        for device in ACTIONS:
            source = self.http.next_device(device)
            if source is None:
                continue
            if self.journal.get(source['id']):
                self.journal.fault('HTTP returned a previously finished command')
                return
            try:
                c = self.make_command(source)
            except (ValueError, KeyError, TypeError) as exc:
                self.http.complete_device({'device':device,'command_id':source['id']},
                                          {'success':False,'detail':('Invalid command/mapping: '+str(exc))[:500]})
                self.journal.fault('Invalid HTTP command or item mapping')
                return
            self.journal.record(c)
            self.active = c
            self.active_device_session = next(s['session_id'] for (d, _), (s, at) in self.statuses.items()
                                              if d == device and self.mono()-at <= 3)
            self.deadline = self.mono()+c['expires_at']-self.wall()-1
            self.next_publish = self.mono()+1
            self.emit('/picking/'+device+'/command', c)
            self.detail = 'Active '+device+' '+c['command_id']
            return

    def disconnected(self, detail):
        self.health_at = float('-inf')
        self.healthy_since = None
        self.detail = detail[:500]
        self.fail_active('HTTP connection lost; cancel requested; physical stop not guaranteed')
