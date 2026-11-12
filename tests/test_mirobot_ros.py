"""Opt-in DDS test against the referenced Mirobot package in simulation mode."""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from tools.mirobot_bridge import Bridge, Journal, RosPickPlace, WarehouseHTTP, load_goal


@pytest.mark.skipif(os.getenv('MIROBOT_ROS_INTEGRATION') != '1',
                    reason='Requires sourced ROS2 Humble and the Mirobot workspace')
def test_real_http_to_repository_ros_action_and_back(tmp_path, monkeypatch):
    # An isolated DDS domain and an explicitly simulated controller never open
    # the real robot's domain or serial port.
    monkeypatch.setenv('ROS_DOMAIN_ID', '178')
    monkeypatch.setenv('ROS_LOCALHOST_ONLY', '1')
    root = Path(__file__).resolve().parents[1]
    config = tmp_path / 'robot-simulation.json'
    pose = {'x': 0.0, 'y': 0.0, 'z': 0.0, 'rx': 0.0, 'ry': 0.0, 'rz': 0.0}
    config.write_text(json.dumps({
        'simulation_mode': True, 'hardware_enable': False, 'coordinates_confirmed': False,
        'homing_enable': False, 'poses': {'home': pose}, 'simulation_step_delay_sec': 0.01,
        'pump_settle_sec': 0.0, 'release_settle_sec': 0.0,
    }))
    with socket.create_server(('127.0.0.1', 0)) as probe:
        port = probe.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    opener = build_opener(ProxyHandler({}))

    def request(method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = Request(base + path, data=data, method=method,
                      headers={'Content-Type': 'application/json', 'X-API-Key': 'app-test',
                               'X-Device-Key': 'device-test'})
        with opener.open(req, timeout=3) as response:
            return response.status, json.load(response)

    env = dict(os.environ, SIMULATION='false', API_KEY='app-test', DEVICE_KEY='device-test',
               DB_PATH=str(tmp_path / 'server.sqlite3'), CATALOG_PATH=str(root / 'catalog.json'),
               CLEAR_ORDERS_ON_START='false', REQUIRE_BOX_CONFIRMATION='true', COMMAND_TIMEOUT='30')
    processes = []
    robot = None
    with (tmp_path / 'http.log').open('w+') as http_log, (tmp_path / 'ros.log').open('w+') as ros_log:
        try:
            processes.append(subprocess.Popen(
                [str(root / '.venv/bin/python'), str(root / 'tools/run_server.py'), '--port', str(port)],
                cwd=root, env=env, stdout=http_log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL))
            processes.append(subprocess.Popen(
                [sys.executable, '-c', 'from mirobot_pick_place.pick_place_action_server import main; main()',
                 '--ros-args', '-p', f'config_file:={config}'],
                cwd=root, env=env, stdout=ros_log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL))
            for _ in range(100):
                try:
                    assert request('GET', '/health')[1]['mode'] == 'real'
                    break
                except URLError:
                    time.sleep(0.05)
            else:
                pytest.fail('HTTP test server did not become ready')
            goal = load_goal(root / 'config/mirobot_goal.example.json')
            robot = RosPickPlace(goal, action_timeout=15, discovery_timeout=10)
            assert 'initialized=True' in robot.ready()
            status, accepted = request('POST', '/orders/app-inventor', {
                'request_id': 'ros-integration', 'orders': [
                    ['ros-customer', {'A': 1}, {'B': 0}, {'C': 0}, {'D': 0}]]})
            assert status == 202
            order_id = accepted['orders'][0]['order_id']
            assert accepted['orders'][0]['status'] == 'waiting'
            assert request('POST', f'/orders/{order_id}/start')[0] == 200

            # Only motor/camera are test doubles; the robot uses the actual
            # repository ActionServer and generated PickPlace interface.
            for device in ('motor', 'camera'):
                for _ in range(100):
                    command = request('GET', f'/devices/{device}/commands/next')[1]['command']
                    if command:
                        break
                    time.sleep(0.05)
                assert command
                assert command['expires_at'] == command['created'] + 30
                assert 0 < command['expires_in'] <= 30
                assert request('POST', f'/devices/{device}/commands/{command["id"]}/complete',
                               {'success': True, 'detail': 'test double'})[0] == 200

            with Journal(tmp_path / 'bridge.sqlite3') as journal:
                bridge = Bridge(WarehouseHTTP(base, 'device-test'), robot, journal)
                for _ in range(100):
                    bridge.step()
                    order = request('GET', f'/orders/{order_id}')[1]
                    if order['status'] == 'waiting_box':
                        break
                    time.sleep(0.05)
                assert order['status'] == 'waiting_box'
                robot_command = next(cmd for cmd in order['commands'] if cmd['device'] == 'robot')
                outcome = json.loads(robot_command['result'])
                assert outcome['success'] is True
                assert 'Robot returned to HOME' in outcome['detail']
                assert journal.pending() is None
            assert request('POST', f'/orders/{order_id}/box-ready')[0] == 200
            assert request('GET', f'/orders/{order_id}')[1]['status'] == 'completed'
            checked = subprocess.run(
                [sys.executable, str(root / 'tools/mirobot_bridge.py'), '--check', '--url', base],
                cwd=root, env=env, capture_output=True, text=True, timeout=20)
            assert checked.returncode == 0, checked.stdout + checked.stderr
            assert '미로봇:' in checked.stdout
            ros_log.flush()
            ros_log.seek(0)
            assert ros_log.read().count('New Pick & Place goal accepted.') == 1
        finally:
            if robot is not None:
                robot.close()
            for process in reversed(processes):
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
