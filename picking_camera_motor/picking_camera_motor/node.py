import json
import queue
import threading
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, HistoryPolicy, ReliabilityPolicy, DurabilityPolicy
from std_msgs.msg import String
import yaml

from .core import Engine
from .backend import Simulation, Real


def qos():
    return QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=10,
                      reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.VOLATILE)


class CameraMotorNode(Node):
    def __init__(self):
        super().__init__('camera_motor_node')
        defaults = dict(simulation=True, display=False, camera=0, port='/dev/ttyACM0', baud=115200,
                        pwm=100, tolerance_x=20.0, tolerance_y=60.0, stable_seconds=0.5,
                        marker_timeout=2.0, move_timeout=20.0, mapping_file='',
                        database=str(Path.home()/'.local/state/picking_camera_motor/state.sqlite3'),
                        local_control_file='/tmp/picking_camera_motor.prepare', preparation_image='',
                        motion_algorithm='original')
        self.cfg = {k:self.declare_parameter(k,v).value for k,v in defaults.items()}
        for k in ('tolerance_x','tolerance_y','stable_seconds','marker_timeout','move_timeout'):
            if self.cfg[k] <= 0: raise ValueError(k+' must be positive')
        self.inbox, self.outbox = queue.Queue(maxsize=1000), queue.Queue()
        self.events = self.create_publisher(String, '/picking/device_events', qos())
        self.status = self.create_publisher(String, '/picking/device_status', qos())
        self.subscriptions_owned = []
        for kind, topic in [('motor','/picking/motor/command'),('camera','/picking/camera/command'),
                            ('heartbeat','/picking/server_heartbeat'),('ack','/picking/result_ack'),('cancel','/picking/cancel')]:
            self.subscriptions_owned.append(self.create_subscription(String, topic, lambda m,k=kind:self.enqueue(k,m.data), qos()))
        self.done = threading.Event()
        self.worker = threading.Thread(target=self.run_owner, daemon=True)
        self.worker.start()
        self.create_timer(0.02, self.flush)

    def enqueue(self, kind, raw):
        try: self.inbox.put_nowait((kind,raw))
        except queue.Full: self.get_logger().error('input queue full; command dropped, retry same ID')

    def run_owner(self):
        engine = backend = None
        try:
            from ament_index_python.packages import get_package_share_directory
            file = self.cfg['mapping_file'] or str(Path(get_package_share_directory('picking_camera_motor'))/'config/items.yaml')
            with open(file) as f: mapping = yaml.safe_load(f)['items']
            # Acquire process lock before opening either hardware resource.
            backend = Simulation(**self.cfg)
            engine = Engine(self.cfg['database'], backend, self.outbox.put, mapping, self.cfg['simulation'])
            if not self.cfg['simulation']:
                backend = Real(**self.cfg)
                engine.backend = backend
                if engine.fault or engine.db.execute('SELECT 1 FROM commands WHERE ack=0').fetchone():
                    backend.stop()  # persisted faults/results must not start automatic homing
            control = Path(self.cfg['local_control_file'])
            if control.exists(): control.unlink()  # stale request must never prepare on startup
            last_diagnostic = 0.0
            while not self.done.is_set():
                for _ in range(100):
                    try: kind,raw = self.inbox.get_nowait()
                    except queue.Empty: break
                    engine.receive(kind,raw)
                if control.exists():
                    request = control.read_text().strip()
                    control.unlink()
                    if not engine.active and not engine.db.execute('SELECT 1 FROM commands WHERE ack=0').fetchone():
                        if request in ('prepare 1','prepare 2','prepare 3','prepare 4'):
                            if self.cfg['simulation']: backend.ready = True
                            else: backend.prepare(int(request[-1]))
                            engine.fault = False
                            engine.detail = 'local preparation requested; fresh centered image required'
                            engine.db.execute("DELETE FROM meta WHERE key='fault'")
                            engine.db.commit()
                        else: engine.error('invalid local preparation request')
                engine.tick()
                diagnostic = getattr(backend, 'alignment_diagnostics', None)
                if diagnostic is not None and time.monotonic()-last_diagnostic >= 1:
                    self.get_logger().info('alignment: '+json.dumps(diagnostic))
                    last_diagnostic = time.monotonic()
                self.done.wait(0.01)
        except Exception as exc:
            self.get_logger().error('owner failed: '+str(exc))
            if engine and engine.active:
                engine.abort('owner failed: '+str(exc))
            elif backend:
                try: backend.stop()
                except Exception: pass
            # Keep fault statuses live even after initialization error.
            from uuid import uuid4
            session = engine.session if engine else str(uuid4())
            while not self.done.wait(1):
                for d in ('motor','camera'):
                    self.outbox.put(dict(v=1,device_id='camera_motor_laptop',device=d,session_id=session,
                        mode='simulation' if self.cfg['simulation'] else 'real',state='fault',active_command_id=None,
                        detail=('owner failed: '+str(exc))[:500],sent_at=time.time()))
        finally:
            if engine: engine.close()
            elif backend: backend.close()

    def flush(self):
        for _ in range(200):
            try: e = self.outbox.get_nowait()
            except queue.Empty: break
            pub = self.events if 'phase' in e else self.status
            pub.publish(String(data=json.dumps(e,ensure_ascii=False)))

    def destroy_node(self):
        self.done.set()
        self.worker.join(timeout=4)
        self.flush()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = CameraMotorNode()
    try: rclpy.spin(node)
    except KeyboardInterrupt: pass
    finally:
        node.destroy_node()
        if rclpy.ok(): rclpy.shutdown()
