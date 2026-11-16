"""Read-only HTTP and ROS2 device readiness check. No publishers or motion calls."""
import argparse
import json
from pathlib import Path
import time

try:
    from .mirobot_bridge import load_device_key
    from .picking_relay import DeviceHTTP, Relay, decode, number, uid
    from .picking_server import qos
except ImportError:
    from mirobot_bridge import load_device_key
    from picking_relay import DeviceHTTP, Relay, decode, number, uid
    from picking_server import qos


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--env-file',type=Path,default=Path(__file__).resolve().parents[1]/'.env')
    parser.add_argument('--mode',choices=('simulation','real'),default='simulation')
    args=parser.parse_args()
    http=DeviceHTTP(args.url,load_device_key(args.env_file))
    health=http.health()
    print('HTTP:',json.dumps(health,ensure_ascii=False))
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String
    rclpy.init(args=[])
    node=Node('picking_readonly_check')
    # status validation/readiness is pure; no journal access or publishing occurs.
    relay=Relay(http,None,{},lambda *_:None,mode=args.mode)
    heartbeats={}
    def status(msg):
        try: relay.status(msg.data)
        except (ValueError,TypeError,KeyError) as exc:
            node.get_logger().warning(str(exc))
    subscription=node.create_subscription(String,'/picking/device_status',status,qos())
    def heartbeat(msg):
        try:
            hb=decode(msg.data)
            uid(hb.get('server_session_id'))
            if not number(hb.get('sent_at')) or abs(hb['sent_at']-time.time())>1:
                raise ValueError('relay clock skew exceeds 1s')
            heartbeats[hb['server_session_id']]=time.monotonic()
        except (ValueError,TypeError,KeyError) as exc:
            node.get_logger().warning(str(exc))
    heartbeat_sub=node.create_subscription(String,'/picking/server_heartbeat',heartbeat,qos())
    def connected():
        return relay.ready() and sum(time.monotonic()-t<=3 for t in heartbeats.values())==1
    try:
        end=time.monotonic()+10
        while time.monotonic()<end and not connected():
            rclpy.spin_once(node,timeout_sec=.2)
        for (device,session),(s,received) in sorted(relay.statuses.items()):
            print(device, json.dumps(s,ensure_ascii=False))
        print('ROS relay heartbeat:', 'fresh' if any(time.monotonic()-t<=3 for t in heartbeats.values()) else 'missing')
        ok=(connected() and health.get('device_transport')=='topics'
            and health.get('mode')==args.mode and health.get('worker_alive') and not health.get('fault'))
        print('연결 점검 성공 (명령 전송 없음)' if ok else '연결 준비 미완료: 장치 상태·모드·시각·서버 설정 확인')
        return 0 if ok else 1
    finally:
        node.destroy_node()
        if rclpy.ok(): rclpy.shutdown()


if __name__=='__main__':
    raise SystemExit(main())
