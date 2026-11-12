import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import pytest


def test_launcher_serves_ipv4_and_ipv6_with_one_database(tmp_path):
    if not socket.has_dualstack_ipv6():
        pytest.skip('This environment cannot create a dual-stack socket')
    with socket.create_server(('::', 0), family=socket.AF_INET6, dualstack_ipv6=True) as probe:
        port = probe.getsockname()[1]
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, DB_PATH=str(tmp_path / 'launch.sqlite3'), SIMULATION='true',
               API_KEY='launch-test-key', CLEAR_ORDERS_ON_START='false',
               REQUIRE_BOX_CONFIRMATION='true', CATALOG_PATH=str(root / 'catalog.json'))
    opener = build_opener(ProxyHandler({}))

    def request(host, path, body=None, authenticated=True):
        data = None if body is None else json.dumps(body).encode()
        headers = {'Content-Type': 'application/json'}
        if authenticated:
            headers['X-API-Key'] = 'launch-test-key'
        req = Request(f'http://{host}:{port}{path}', data=data, headers=headers)
        try:
            response = opener.open(req, timeout=1)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    with (tmp_path / 'server.log').open('w+') as log:
        process = subprocess.Popen(
            [sys.executable, str(root / 'tools/run_server.py'), '--port', str(port)],
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                try:
                    status, health = request('127.0.0.1', '/health')
                    break
                except URLError:
                    if process.poll() is not None:
                        log.seek(0)
                        pytest.fail(log.read())
                    time.sleep(0.05)
            else:
                pytest.fail('Server did not become ready')
            assert status == 200
            assert health == {'mode': 'simulation', 'fault': None, 'worker_alive': True}
            assert request('[::1]', '/health') == (200, health)
            assert request('[::1]', '/orders', authenticated=False)[0] == 401

            # An IPv4 client writes an order that the IPv6 client can read.
            status, accepted = request('127.0.0.1', '/orders/app-inventor', {
                'request_id': 'dual-stack-test', 'orders': [
                    ['dual-stack-customer', {'A': 1}, {'B': 0}, {'C': 0}, {'D': 0}]]})
            assert status == 202
            order_id = accepted['orders'][0]['order_id']
            status, order = request('[::1]', f'/orders/{order_id}')
            assert status == 200
            assert order['status'] == 'waiting'
            assert order['commands'] == []
            assert order['customer_id'] == 'dual-stack-customer'

            # The admin workflow also works through IPv6 with the same worker.
            status, result = request('[::1]', f'/orders/{order_id}/start', {})
            assert status == 200
            assert result['accepted'] is True
            for _ in range(100):
                _, order = request('127.0.0.1', f'/orders/{order_id}')
                if order['status'] == 'waiting_box':
                    break
                time.sleep(0.05)
            assert order['status'] == 'waiting_box'
            assert request('[::1]', f'/orders/{order_id}/box-ready', {})[0] == 200
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        # Uvicorn may re-raise SIGTERM after completing graceful shutdown.
        assert process.returncode in (0, -signal.SIGTERM)
        log.seek(0)
        assert 'Application shutdown complete.' in log.read()
