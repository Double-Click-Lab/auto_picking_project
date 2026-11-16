"""Run on the server laptop: local HTTP device API <-> ROS2 picking topics."""

import argparse
import json
from pathlib import Path
import queue
import threading
import time

try:
    from .mirobot_bridge import load_device_key
    from .picking_relay import DeviceHTTP, Relay, RelayJournal, load_mapping
except ImportError:
    from mirobot_bridge import load_device_key
    from picking_relay import DeviceHTTP, Relay, RelayJournal, load_mapping


def qos():
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    return QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                      durability=DurabilityPolicy.VOLATILE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--env-file', type=Path, default=Path(__file__).resolve().parents[1]/'.env')
    parser.add_argument('--mode', choices=('simulation','real'), default='simulation')
    parser.add_argument('--mapping', type=Path, required=True,
                        help='JSON {items: {item_id/bin: marker_id}}, shared with camera')
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--recover', action='store_true', help='Clear relay fault after operator reconciliation')
    args = parser.parse_args()
    mapping = load_mapping(args.mapping)
    key = load_device_key(args.env_file)
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String

    class ServerNode(Node):
        def __init__(self):
            super().__init__('picking_server_relay')
            self.inbox, self.outbox = queue.Queue(maxsize=2000), queue.Queue()
            self.done = threading.Event()
            self.healthy_until = 0
            self.session = None
            self.collision = False
            names = ['/picking/'+d+'/command' for d in ('motor','camera','robot')]
            names += ['/picking/server_heartbeat','/picking/result_ack','/picking/cancel']
            self.pubs = {topic:self.create_publisher(String, topic, qos()) for topic in names}
            self.subs = [self.create_subscription(String, '/picking/device_'+kind,
                         lambda msg,k=kind:self.enqueue(k,msg.data), qos()) for kind in ('events','status')]
            self.worker = threading.Thread(target=self.owner, daemon=True)
            self.worker.start()
            self.create_timer(0.02, self.flush)
            self.create_timer(1.0, self.heartbeat)

        def enqueue(self, kind, raw):
            try:
                self.inbox.put_nowait((kind, raw))
            except queue.Full:
                self.get_logger().error('Input queue full; result will be retried by device')

        def owner(self):
            journal = relay = None
            try:
                journal = RelayJournal(args.journal)
                http = DeviceHTTP(args.url, key, timeout=1)
                if args.recover:
                    health = http.health()
                    if health.get('fault') or not health.get('worker_alive'):
                        raise ValueError('Reconcile devices and reset HTTP system fault before --recover')
                    journal.recover()
                relay = Relay(http, journal, mapping, lambda t,e:self.outbox.put((t,e)), args.mode)
                self.session = relay.session
                previous = None
                while not self.done.is_set():
                    for _ in range(200):
                        try:
                            kind, raw = self.inbox.get_nowait()
                        except queue.Empty:
                            break
                        try:
                            (relay.event if kind == 'events' else relay.status)(raw)
                        except (ValueError, TypeError, KeyError, OverflowError) as exc:
                            self.get_logger().warning('Ignored protocol message: '+str(exc))
                    try:
                        if self.collision:
                            relay.fail_active('Multiple command/heartbeat publishers detected')
                            journal.fault('Multiple command/heartbeat publishers detected')
                        relay.tick()
                        self.healthy_until = relay.health_at+2 if not journal.fault_detail else 0
                    except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
                        relay.disconnected(type(exc).__name__+': '+str(exc))
                        self.healthy_until = 0
                    if relay.detail != previous:
                        self.get_logger().info(relay.detail)
                        previous = relay.detail
                    self.done.wait(0.2)
            except Exception as exc:
                self.get_logger().error('Relay stopped: '+str(exc))
                self.healthy_until = 0
            finally:
                if relay:
                    relay.fail_active('Relay shutting down; physical stop not guaranteed')
                    try:
                        relay.report()
                    except Exception:
                        pass  # saved result is replayed at next startup
                if journal:
                    journal.close()

        def heartbeat(self):
            self.collision = any(self.count_publishers(t) > 1 for t in self.pubs
                                 if t.endswith('/command') or t.endswith('/server_heartbeat'))
            if self.session and not self.collision and time.monotonic() < self.healthy_until:
                self.pubs['/picking/server_heartbeat'].publish(String(data=json.dumps(
                    dict(v=1, server_session_id=self.session, sent_at=time.time()))))

        def flush(self):
            for _ in range(200):
                try:
                    topic, event = self.outbox.get_nowait()
                except queue.Empty:
                    break
                self.pubs[topic].publish(String(data=json.dumps(event, ensure_ascii=False)))

        def destroy_node(self):
            self.healthy_until = 0
            self.done.set()
            self.worker.join(timeout=8)
            self.flush()
            return super().destroy_node()

    rclpy.init(args=[])
    node = None
    try:
        node = ServerNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
