"""출고 서버의 robot 명령을 ROS2 /pick_place 액션으로 전달합니다."""

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import sqlite3
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID


GOAL_FIELDS = tuple(f'{pose}_{axis}' for pose in ('pick', 'place')
                    for axis in ('x', 'y', 'z', 'rx', 'ry', 'rz')) + ('approach_height',)


def load_goal(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or set(data) != set(GOAL_FIELDS):
        raise ValueError('좌표 파일에는 pick/place의 x,y,z,rx,ry,rz와 approach_height가 필요합니다.')
    for key, value in data.items():
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f'{key}: 유한한 숫자가 필요합니다.')
    if data['approach_height'] <= 0:
        raise ValueError('approach_height는 양수여야 합니다.')
    return {key: float(value) for key, value in data.items()}


def load_device_key(path):
    if os.getenv('DEVICE_KEY'):
        return os.environ['DEVICE_KEY']
    if Path(path).is_file():
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            line = line.strip().removeprefix('export ')
            name, separator, value = line.partition('=')
            if separator and name.strip() == 'DEVICE_KEY':
                tokens = shlex.split(value, comments=True)
                if len(tokens) != 1:
                    raise ValueError('DEVICE_KEY 설정을 확인하세요.')
                return tokens[0]
    raise ValueError('DEVICE_KEY 환경변수 또는 --env-file에 장치 키를 설정하세요.')


def result(success, detail):
    return {'success': bool(success), 'detail': str(detail)[:500]}


class WarehouseHTTP:
    def __init__(self, url, device_key, timeout=3.0):
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise ValueError('--url에는 경로 없는 서버 기본 주소를 입력하세요.')
        self.url = url.rstrip('/')
        self.device_key = device_key
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method, path, body=None):
        encoded = None if body is None else json.dumps(body).encode()
        req = Request(self.url + path, data=encoded, method=method,
                      headers={'Content-Type': 'application/json', 'X-Device-Key': self.device_key})
        with self.opener.open(req, timeout=self.timeout) as response:
            return json.load(response)

    def health(self):
        return self.request('GET', '/health')

    def next_command(self):
        started = time.monotonic()
        command = self.request('GET', '/devices/robot/commands/next')['command']
        if command is not None and 'expires_in' in command:
            # A conservative local deadline, independent of clock skew between
            # the Raspberry Pi and robot PC. Include the HTTP round-trip time.
            command['_bridge_deadline'] = started + command['expires_in']
        return command

    def complete(self, command_id, outcome):
        response = self.request('POST', '/devices/robot/commands/'
                                + quote(command_id, safe='') + '/complete', outcome)
        if response.get('accepted') is not True:
            raise RuntimeError('서버가 완료 보고를 승인하지 않았습니다.')


class Journal:
    """실행 전에 ID를 저장하고, 결과 보고 재시도 중에는 팔을 다시 움직이지 않습니다."""

    def __init__(self, path):
        self.lock = open(str(path) + '.lock', 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError('같은 기록 파일을 사용하는 브리지가 이미 실행 중입니다.') from None
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS executions ('
                            'id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, '
                            'result TEXT, acknowledged INTEGER NOT NULL DEFAULT 0)')
            # An interrupted execution has an unknown physical outcome. Never
            # send its RUN again; report failure for operator reconciliation.
            self.db.execute('UPDATE executions SET result=? WHERE result IS NULL',
                            (json.dumps(result(False, '이전 브리지가 실행 중 중단되었습니다. '
                                               '중복 동작을 막기 위해 재실행하지 않습니다. 실제 상태를 확인하세요.')),))

    def close(self):
        self.db.close()
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def pending(self):
        row = self.db.execute('SELECT * FROM executions WHERE acknowledged=0 ORDER BY rowid LIMIT 1').fetchone()
        return dict(row) if row else None

    def claim(self, command):
        fingerprint = hashlib.sha256(json.dumps(command, sort_keys=True).encode()).hexdigest()
        row = self.db.execute('SELECT * FROM executions WHERE id=?', (command['id'],)).fetchone()
        if row:
            if row['fingerprint'] != fingerprint:
                raise RuntimeError('같은 명령 ID의 내용이 변경되었습니다. 상태 확인이 필요합니다.')
            return json.loads(row['result'])
        with self.db:
            self.db.execute('INSERT INTO executions (id, fingerprint) VALUES (?, ?)',
                            (command['id'], fingerprint))
        return None

    def finish(self, command_id, outcome):
        with self.db:
            self.db.execute('UPDATE executions SET result=? WHERE id=?',
                            (json.dumps(outcome), command_id))

    def acknowledge(self, command_id):
        with self.db:
            self.db.execute('UPDATE executions SET acknowledged=1 WHERE id=?', (command_id,))


class Bridge:
    def __init__(self, http, robot, journal):
        self.http, self.robot, self.journal = http, robot, journal

    def step(self):
        pending = self.journal.pending()
        if pending:
            self.http.complete(pending['id'], json.loads(pending['result']))
            self.journal.acknowledge(pending['id'])
            return '완료 보고 재전송 성공'
        command = self.http.next_command()
        if command is None:
            return None
        # Ignore transport metadata, which can change on subsequent polling.
        identity = {key: command.get(key) for key in ('id', 'order_id', 'device', 'action', 'payload')}
        previous = self.journal.claim(identity)
        if previous is not None:
            outcome = previous
        else:
            try:
                UUID(command['id'])
                if (command.get('device') != 'robot' or command.get('action') != 'pick_and_place'
                        or type(command.get('payload', {}).get('quantity')) is not int
                        or command['payload']['quantity'] != 1):
                    raise ValueError('robot/pick_and_place/quantity=1 명령만 지원합니다.')
                deadline = command.get('_bridge_deadline')
                if deadline is None:
                    raise ValueError('서버를 최신 코드로 재시작하세요: expires_in이 필요합니다.')
                outcome = self.robot.execute(command['id'], deadline)
            except Exception as exc:
                outcome = result(False, f'{type(exc).__name__}: {exc}')
            self.journal.finish(command['id'], outcome)
        self.http.complete(command['id'], outcome)
        self.journal.acknowledge(command['id'])
        return f"명령 {command['id']}: {'성공' if outcome['success'] else '실패'} / {outcome['detail']}"


class RosPickPlace:
    def __init__(self, goal, action_timeout=45.0, discovery_timeout=5.0):
        # The HTTP server itself does not import or require ROS packages.
        import rclpy
        from rclpy.action import ActionClient
        from rclpy.executors import SingleThreadedExecutor
        from mirobot_pick_place_interfaces.action import PickPlace
        from std_srvs.srv import Trigger

        self.rclpy = rclpy
        self.action_type, self.trigger_type = PickPlace, Trigger
        self.goal_values = goal
        self.action_timeout, self.discovery_timeout = action_timeout, discovery_timeout
        rclpy.init(args=[])
        self.node = rclpy.create_node('warehouse_mirobot_bridge')
        self.client = ActionClient(self.node, PickPlace, '/pick_place')
        self.status_client = self.node.create_client(Trigger, '/mirobot/status')
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.thread = threading.Thread(target=self.executor.spin, daemon=True)
        self.thread.start()

    def close(self):
        self.executor.shutdown(timeout_sec=2.0)
        self.thread.join(timeout=2.0)
        self.client.destroy()
        self.node.destroy_node()
        self.rclpy.shutdown()

    def wait(self, future, timeout):
        completed = threading.Event()
        future.add_done_callback(lambda _: completed.set())
        if not completed.wait(max(0.0, timeout)):
            raise TimeoutError('ROS2 응답 시간 초과')
        return future.result()

    def ready(self, timeout=None):
        timeout = self.discovery_timeout if timeout is None else timeout
        end = time.monotonic() + timeout
        if not self.client.wait_for_server(timeout_sec=timeout):
            raise RuntimeError('/pick_place 액션 서버를 찾을 수 없습니다.')
        if not self.status_client.wait_for_service(timeout_sec=max(0.0, end - time.monotonic())):
            raise RuntimeError('/mirobot/status 서비스를 찾을 수 없습니다.')
        response = self.wait(self.status_client.call_async(self.trigger_type.Request()),
                             end - time.monotonic())
        if not response.success:
            raise RuntimeError('로봇 초기화 또는 복구가 필요합니다: ' + response.message)
        return response.message

    def execute(self, command_id, deadline):
        from action_msgs.msg import GoalStatus
        from unique_identifier_msgs.msg import UUID as GoalUUID

        remaining = deadline - time.monotonic() - 2.0
        if remaining <= 0:
            return result(False, '서버 명령의 유효 시간이 부족해 RUN을 보내지 않았습니다.')
        end = time.monotonic() + min(self.action_timeout, remaining)
        self.ready(min(self.discovery_timeout, max(0.0, end - time.monotonic())))
        goal = self.action_type.Goal()
        goal.start = True
        for key, value in self.goal_values.items():
            setattr(goal, key, value)
        # Reusing the HTTP command UUID adds ROS-side duplicate rejection too.
        goal_uuid = GoalUUID(uuid=list(UUID(command_id).bytes))
        handle = None
        sent = None
        try:
            if end <= time.monotonic():
                raise TimeoutError('명령 유효 시간 부족')
            sent = self.client.send_goal_async(goal, goal_uuid=goal_uuid,
                                                feedback_callback=self.feedback)
            handle = self.wait(sent, end - time.monotonic())
            if handle is None or not handle.accepted:
                return result(False, '미로봇이 실행 요청을 거절했습니다.')
            wrapped = self.wait(handle.get_result_async(), end - time.monotonic())
            success = wrapped.status == GoalStatus.STATUS_SUCCEEDED and wrapped.result.success
            return result(success, wrapped.result.message)
        except BaseException:
            # Cancel at the robot's supported operation boundary. Cancellation
            # is not an emergency stop and must not cause a second RUN.
            if handle is not None and handle.accepted:
                try:
                    self.wait(handle.cancel_goal_async(), 1.0)
                except Exception:
                    pass
            elif sent is not None:
                def cancel_late(future):
                    try:
                        late = future.result()
                        if late is not None and late.accepted:
                            late.cancel_goal_async()
                    except Exception:
                        pass
                sent.add_done_callback(cancel_late)
            raise

    def feedback(self, message):
        data = message.feedback
        print(f'미로봇 {data.current_step}/{data.total_steps}: {data.current_state} '
              f'({data.progress:.0f}%)', flush=True)


def positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError('양의 유한한 숫자가 필요합니다.')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='HTTP/ROS 상태 조회 후 종료 (동작 없음)')
    mode.add_argument('--run', action='store_true', help='서버 명령을 조회하고 로봇 동작 요청')
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--env-file', type=Path, default=Path(__file__).resolve().parents[1] / '.env')
    parser.add_argument('--goal-file', type=Path, help='검증된 고정 PickPlace 좌표 JSON 파일')
    parser.add_argument('--journal', type=Path, default=Path('mirobot-bridge.sqlite3'))
    parser.add_argument('--action-timeout', type=positive, default=45.0)
    parser.add_argument('--interval', type=positive, default=1.0)
    args = parser.parse_args()
    if args.run and args.goal_file is None:
        parser.error('--run에는 --goal-file이 필요합니다.')
    robot = None
    try:
        goal = load_goal(args.goal_file) if args.goal_file else None
        http = WarehouseHTTP(args.url, load_device_key(args.env_file))
        health = http.health()
        print('출고 서버:', json.dumps(health, ensure_ascii=False), flush=True)
        if args.run and (health['mode'] != 'real' or health['fault'] or not health['worker_alive']):
            raise ValueError('서버의 SIMULATION=false, fault 없음, worker_alive=true를 확인하세요.')
        robot = RosPickPlace(goal, args.action_timeout)
        print('미로봇:', robot.ready(), flush=True)
        if args.check:
            if health['mode'] == 'simulation':
                print('서버가 모의 모드라 장치 명령은 전달하지 않습니다. 연결 조회만 완료했습니다.')
            return 0
        with Journal(args.journal) as journal:
            bridge = Bridge(http, robot, journal)
            last_error = None
            while True:
                try:
                    message = bridge.step()
                    if message:
                        print(message, flush=True)
                    last_error = None
                except (HTTPError, URLError, TimeoutError) as exc:
                    message = f'HTTP 통신 오류 ({type(exc).__name__}): {exc}'
                    if message != last_error:
                        print(message, file=sys.stderr, flush=True)
                    last_error = message
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\n브리지 종료. 중단된 동작은 기록을 바탕으로 재실행을 차단합니다.')
        return 0
    except (ImportError, ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, f'{exc}\nROS2 Humble와 미로봇 workspace를 source한 /usr/bin/python3로 실행하세요.\n')
    finally:
        if robot is not None:
            robot.close()


if __name__ == '__main__':
    raise SystemExit(main())
