"""Simulation-only protocol peer for server tests. Never imports robot/serial drivers."""

import argparse
import json
from pathlib import Path
import time
from uuid import uuid4

try:
    from .picking_relay import RelayJournal, decode, number, uid
except ImportError:
    from picking_relay import RelayJournal, decode, number, uid


class SimRobot:
    def __init__(self, path, emit, wall=time.time, mono=time.monotonic):
        self.journal = RelayJournal(path)
        self.emit, self.wall, self.mono = emit, wall, mono
        self.session = str(uuid4())
        self.hb = None
        self.active = None
        self.starts = 0
        self.last_publish = float('-inf')
        # RelayJournal startup failures are converted to durable v1 device results.
        for row in self.journal.db.execute('SELECT * FROM relay_commands WHERE result IS NOT NULL AND event IS NULL').fetchall():
            self.finish(json.loads(row['body']), False, json.loads(row['result'])['detail'])

    def finish(self, c, success, detail):
        event = dict(v=1, event_id=str(uuid4()), phase='result', success=success, detail=detail[:500],
                     **{k:c[k] for k in ('server_session_id','command_id','order_id','device','mode')})
        self.journal.outcome(c['command_id'], {'success':success,'detail':detail[:500]}, event)
        self.emit('/picking/device_events', event)

    def abort(self, detail):
        if self.active:
            self.finish(self.active, False, detail)
            self.active = None
            self.journal.fault(detail)

    def receive(self, kind, raw):
        c = decode(raw)
        if kind == 'heartbeat':
            uid(c.get('server_session_id'))
            if not number(c.get('sent_at')):
                raise ValueError('invalid heartbeat timestamp')
            if self.active and c['server_session_id'] != self.active['server_session_id']:
                self.abort('Server session changed')
            self.hb = c['server_session_id'], self.mono(), c['sent_at']-self.wall()
        elif kind == 'ack':
            row = self.journal.get(c.get('command_id', ''))
            if row and row['event'] and c.get('accepted') is True:
                event = json.loads(row['event'])
                if all(c.get(k) == event[k] for k in ('server_session_id','command_id','device','event_id')):
                    with self.journal.db:
                        self.journal.db.execute('UPDATE relay_commands SET posted=1 WHERE id=?', (row['id'],))
        elif kind == 'cancel':
            if self.active and all(c.get(k) == self.active[k] for k in ('server_session_id','command_id','device')):
                self.abort('Canceled by server')
        elif kind == 'robot':
            for key in ('server_session_id','command_id','order_id'):
                uid(c.get(key))
            p = c.get('payload')
            if c.get('device') != 'robot' or c.get('action') != 'pick_and_place' or c.get('mode') not in ('real','simulation'):
                raise ValueError('wrong robot command')
            if not isinstance(p, dict) or type(p.get('quantity')) is not int or p['quantity'] != 1 or p.get('destination_id') != c['order_id']:
                raise ValueError('invalid payload')
            if not all(isinstance(p.get(k), str) and p[k] for k in ('item_id','bin')):
                raise ValueError('missing item/bin')
            if not all(number(c.get(k)) for k in ('issued_at','expires_at')) or c['issued_at'] >= c['expires_at']:
                raise ValueError('invalid command timestamps')
            row = self.journal.get(c['command_id'])
            if row:
                if row['body'] != json.dumps(c, sort_keys=True):
                    raise ValueError('same ID with changed contents')
                if row['event']:
                    self.emit('/picking/device_events', json.loads(row['event']))
                return
            reason = None
            if c['mode'] != 'simulation': reason = 'simulation peer refuses real commands'
            elif self.journal.fault_detail: reason = 'operator recovery required'
            elif self.active or self.journal.db.execute('SELECT 1 FROM relay_commands WHERE posted=0').fetchone(): reason = 'busy/unacknowledged result'
            elif not self.hb or self.hb[0] != c['server_session_id'] or self.mono()-self.hb[1] > 5: reason = 'recent matching heartbeat required'
            elif abs(self.hb[2]) > 1 or c['issued_at'] > self.wall()+1: reason = 'clock skew exceeds 1s'
            elif c['expires_at']-self.wall() <= 2: reason = 'command expired'
            self.journal.record(c)
            if reason:
                self.finish(c, False, reason)
                return
            self.active = c
            self.starts += 1
            self.deadline = self.mono()+c['expires_at']-self.wall()-1
            self.finish_at = self.mono()+0.3
            self.emit('/picking/device_events', dict(v=1,event_id=str(uuid4()),phase='accepted',success=None,
                detail='simulation peer accepted; hardware unopened',
                **{k:c[k] for k in ('server_session_id','command_id','order_id','device','mode')}))

    def tick(self):
        if self.active:
            if self.mono() >= self.deadline or self.wall() >= self.active['expires_at']:
                self.abort('Command expired')
            elif not self.hb or self.mono()-self.hb[1] > 5:
                self.abort('Server heartbeat lost')
            elif self.mono() >= self.finish_at:
                self.finish(self.active, True, 'simulation robot finished; no robot hardware opened')
                self.active = None
        if self.mono()-self.last_publish >= 1:
            self.last_publish = self.mono()
            for row in self.journal.db.execute('SELECT event FROM relay_commands WHERE posted=0 AND event IS NOT NULL'):
                self.emit('/picking/device_events', json.loads(row[0]))
            state = 'fault' if self.journal.fault_detail else 'busy' if self.active else 'ready'
            self.emit('/picking/device_status', dict(v=1,device='robot',device_id='mirobot_laptop',
                session_id=self.session,mode='simulation',state=state,
                active_command_id=self.active['command_id'] if self.active else None,
                detail='SIMULATION ONLY; no USB or action calls',sent_at=self.wall()))

    def close(self):
        self.abort('Simulation peer shutting down')
        self.journal.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', type=Path, required=True)
    args = parser.parse_args()
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String
    try:
        from .picking_server import qos
    except ImportError:
        from picking_server import qos
    rclpy.init(args=[])
    node = Node('picking_sim_robot')
    pubs = {t:node.create_publisher(String,t,qos()) for t in ('/picking/device_events','/picking/device_status')}
    peer = SimRobot(args.journal, lambda t,e:pubs[t].publish(String(data=json.dumps(e))))

    def receive(kind, raw):
        try:
            peer.receive(kind, raw)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            node.get_logger().warning('Ignored protocol message: '+str(exc))
    subs = [node.create_subscription(String,t,lambda m,k=k:receive(k,m.data),qos()) for k,t in (
        ('robot','/picking/robot/command'), ('heartbeat','/picking/server_heartbeat'),
        ('ack','/picking/result_ack'), ('cancel','/picking/cancel'))]
    node.create_timer(0.05, peer.tick)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        peer.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
