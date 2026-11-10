"""서버 신호를 읽기만 하는 연속 모니터. Python 표준 라이브러리만 사용합니다."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import time

try:
    from .test_client import Client, load_api_key
except ImportError:
    from test_client import Client, load_api_key


def format_event(event):
    stamp = datetime.fromisoformat(event['time']).astimezone().strftime('%H:%M:%S')
    mock = '[모의 실행] ' if event.get('simulation') else ''
    details = {key: value for key, value in event.items()
               if key not in ('id', 'time', 'kind', 'message', 'simulation')}
    suffix = ' | ' + json.dumps(details, ensure_ascii=False) if details else ''
    return f"[{stamp}] {mock}{event['message']}{suffix}"


class SignalMonitor:
    def __init__(self, client, output=print):
        self.client = client
        self.output = output
        self.cursor = 0
        self.instance = None
        self.health = None
        self.error = None

    def poll(self):
        data = self.client.request('GET', f'/monitor/events?after={self.cursor}')
        restarted = self.instance is not None and data['instance'] != self.instance
        if restarted:
            self.output('서버 재시작 감지. 새 서버의 기록을 조회합니다.')
            self.cursor = 0
            data = self.client.request('GET', '/monitor/events?after=0')
        if self.error is not None:
            self.output('서버 연결 복구')
            self.error = None
        if self.instance is None or restarted:
            self.output(f"서버 연결됨: {self.client.url} / 모드: {data['mode']}")
            for order in data.get('orders', []):
                self.output('현재 작업: ' + json.dumps(order, ensure_ascii=False))
        self.instance = data['instance']
        health = (data['mode'], data['fault'], data['worker_alive'])
        if health != self.health:
            self.output(f"서버 상태: mode={health[0]}, 오류={health[1]}, 작업자 실행={health[2]}")
            self.health = health
        if self.cursor < data['oldest_id'] - 1:
            self.output('서버 기록 보관 범위를 넘어선 이전 이벤트가 있습니다. 남아 있는 기록부터 표시합니다.')
        for event in data['events']:
            self.output(format_event(event))
        self.cursor = data['cursor']

    def run(self, interval):
        try:
            while True:
                try:
                    self.poll()
                except (RuntimeError, ValueError, KeyError) as exc:
                    message = str(exc)
                    if message != self.error:
                        self.output('조회 오류: ' + message)
                        if '404' in message:
                            self.output('새 모니터 API를 적용하려면 서버를 수정된 코드로 재시작하세요.')
                        self.error = message
                time.sleep(interval)
        except KeyboardInterrupt:
            self.output('\n모니터 종료. 서버 작업은 계속됩니다.')


def main():
    parser = argparse.ArgumentParser(description='접속·주문·장치 신호 조회 전용 모니터')
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--env-file', default=str(Path(__file__).resolve().parents[1] / '.env'))
    parser.add_argument('--interval', type=float, default=1.0)
    args = parser.parse_args()
    if not 0.1 <= args.interval <= 60:
        parser.error('--interval은 0.1~60초로 지정하세요.')
    try:
        key, source = load_api_key(args.env_file)
    except ValueError as exc:
        parser.error(str(exc))
    print(f'신호 모니터 / API 키 출처: {source}', flush=True)
    print('주문·장치 제어·상자 확인 없이 조회만 합니다. 종료: Ctrl+C', flush=True)
    print('상자 교체 대기 중에는 새 신호가 올 때까지 출력하지 않습니다.', flush=True)
    SignalMonitor(Client(args.url, key), output=lambda line: print(line, flush=True)).run(args.interval)


if __name__ == '__main__':
    main()
