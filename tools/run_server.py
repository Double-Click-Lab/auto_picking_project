"""IPv4와 IPv6 요청을 하나의 작업자에서 받는 출고 서버 실행 도구."""

import argparse
from pathlib import Path
import socket
import sys

import uvicorn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('--port는 0~65535 범위여야 합니다.')

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    dual_stack = socket.has_dualstack_ipv6()
    host = '::' if dual_stack else '0.0.0.0'
    try:
        listener = socket.create_server(
            (host, args.port),
            family=socket.AF_INET6 if dual_stack else socket.AF_INET,
            dualstack_ipv6=dual_stack,
            backlog=2048,
        )
    except OSError as exc:
        parser.exit(1, f'서버 수신 소켓을 열 수 없습니다: {exc}\n'
                    '같은 포트의 기존 서버가 실행 중인지 확인하세요.\n')

    with listener:
        port = listener.getsockname()[1]
        mode = 'IPv4 + IPv6' if dual_stack else 'IPv4 (이 환경은 IPv6 동시 수신을 지원하지 않습니다)'
        print(f'서버 수신: {mode} / 포트 {port}', flush=True)
        # One process and one worker own the DB for both address families.
        config = uvicorn.Config('app.main:app', host=host, port=port, workers=1)
        uvicorn.Server(config).run(sockets=[listener])


if __name__ == '__main__':
    main()
