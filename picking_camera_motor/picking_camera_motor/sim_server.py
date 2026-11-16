"""Simulation-only sequential test publisher. Never creates robot commands."""
import json
import time
import uuid
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from .node import qos


def command(session, order, device):
    now = time.time()
    return dict(v=1, server_session_id=session, command_id=str(uuid.uuid4()), order_id=order,
                device=device, action='move_to_bin' if device=='motor' else 'align_bin', mode='simulation',
                issued_at=now, expires_at=now+30,
                payload=dict(item_id='ITEM-001',bin='A01',quantity=1,destination_id=order,
                             marker_id=1,position_mm=0.0,motor_length_mm=1000.0))


class Server(Node):
    def __init__(self):
        super().__init__('simulation_server')
        self.session, self.order = str(uuid.uuid4()), str(uuid.uuid4())
        self.hb = self.create_publisher(String,'/picking/server_heartbeat',qos())
        self.ack = self.create_publisher(String,'/picking/result_ack',qos())
        self.pubs = {d:self.create_publisher(String,'/picking/'+d+'/command',qos()) for d in ('motor','camera')}
        self.sub = self.create_subscription(String,'/picking/device_events',self.event,qos())
        self.pending = None
        self.started = time.monotonic()
        self.stage = 'motor'
        self.seen = set()
        self.issued = {}
        self.ok = False
        self.create_timer(1.0,self.tick)
    def send(self,pub,obj): pub.publish(String(data=json.dumps(obj)))
    def tick(self):
        self.send(self.hb,dict(v=1,server_session_id=self.session,sent_at=time.time()))
        if time.monotonic()-self.started < 2: return
        if self.stage == 'done': return
        if self.pending is None:
            self.pending = command(self.session,self.order,self.stage)
            self.issued[self.pending['command_id']] = self.pending
            self.get_logger().info('COMMAND '+json.dumps(self.pending))
        self.send(self.pubs[self.stage],self.pending)
    def event(self,msg):
        try: e=json.loads(msg.data)
        except ValueError: return
        if not isinstance(e,dict) or e.get('server_session_id') != self.session or e.get('command_id') not in self.issued or e.get('phase') != 'result': return
        if e.get('device') != self.issued[e['command_id']]['device'] or e.get('mode') != 'simulation': return
        self.get_logger().info('RESULT '+msg.data)
        self.send(self.ack,dict(v=1,server_session_id=self.session,command_id=e['command_id'],device=e['device'],event_id=e['event_id'],accepted=True))
        if e['event_id'] in self.seen: return
        if not self.pending or e['command_id'] != self.pending['command_id']: return
        self.seen.add(e['event_id'])
        if not e['success']:
            self.stage='done'
            self.get_logger().error('simulation failed')
            return
        if self.stage == 'motor':
            self.stage='camera'
            self.pending=None
        else:
            self.stage='done'
            self.ok=True
            self.get_logger().info('PASS simulation motor -> camera; hardware unopened')


def main(args=None):
    rclpy.init(args=args)
    node=Server()
    try:
        while rclpy.ok() and node.stage != 'done' and time.monotonic()-node.started < 45:
            rclpy.spin_once(node,timeout_sec=0.2)
        # keep ACK publisher alive briefly for DDS delivery
        end=time.monotonic()+1
        while time.monotonic()<end: rclpy.spin_once(node,timeout_sec=0.1)
        if not node.ok: raise RuntimeError('simulation did not complete successfully')
    finally:
        node.destroy_node()
        if rclpy.ok(): rclpy.shutdown()
