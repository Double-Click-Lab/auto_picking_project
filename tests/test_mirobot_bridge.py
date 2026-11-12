import json
import time
from urllib.error import URLError
from uuid import uuid4

import pytest

from tools.mirobot_bridge import Bridge, Journal, WarehouseHTTP, load_device_key, load_goal, result


def command():
    return {'id': str(uuid4()), 'order_id': str(uuid4()), 'device': 'robot',
            'action': 'pick_and_place', 'payload': {'item_id': 'ITEM-001', 'quantity': 1},
            '_bridge_deadline': time.monotonic() + 60}


class HTTP:
    def __init__(self, cmd):
        self.cmd = cmd
        self.reports = []
        self.lose_reply = False

    def next_command(self):
        return self.cmd

    def complete(self, command_id, outcome):
        self.reports.append((command_id, outcome.copy()))
        if self.lose_reply:
            raise URLError('Completion response lost')


class Robot:
    def __init__(self, outcome=None):
        self.calls = []
        self.outcome = outcome or result(True, 'Returned to HOME')

    def execute(self, command_id, deadline):
        self.calls.append((command_id, deadline))
        return self.outcome


def test_success_records_before_run_and_never_repeats_duplicate_command(tmp_path):
    cmd = command()
    http, robot = HTTP(cmd), Robot()
    with Journal(tmp_path / 'bridge.sqlite3') as journal:
        original = robot.execute

        def execute(command_id, deadline):
            pending = journal.pending()
            assert pending['id'] == command_id
            assert pending['result'] is None  # Durable claim before sending RUN.
            assert http.reports == []  # Success is not reported on goal acceptance.
            return original(command_id, deadline)

        robot.execute = execute
        bridge = Bridge(http, robot, journal)
        assert '성공' in bridge.step()
        # Transport timestamps can change while the server re-offers an ID.
        http.cmd = dict(cmd, dispatched_at=time.time(), expires_at=time.time() + 60)
        bridge.step()
        assert len(robot.calls) == 1
        assert http.reports[0] == http.reports[1]
        assert journal.pending() is None


def test_lost_completion_response_retries_same_result_without_motion(tmp_path):
    http, robot = HTTP(command()), Robot()
    path = tmp_path / 'bridge.sqlite3'
    with Journal(path) as journal:
        http.lose_reply = True
        with pytest.raises(URLError):
            Bridge(http, robot, journal).step()
    # Even a process restart must replay the result, never the goal.
    with Journal(path) as journal:
        http.lose_reply = False
        Bridge(http, robot, journal).step()
        assert len(robot.calls) == 1
        assert http.reports[0] == http.reports[1]
        assert http.reports[1][1]['success'] is True


def test_interrupted_goal_is_reported_unknown_and_not_run_again(tmp_path):
    cmd = command()
    path = tmp_path / 'bridge.sqlite3'
    with Journal(path) as journal:
        journal.claim({key: cmd[key] for key in ('id', 'order_id', 'device', 'action', 'payload')})
    http, robot = HTTP(cmd), Robot()
    with Journal(path) as journal:
        Bridge(http, robot, journal).step()
        assert robot.calls == []
        assert http.reports[0][1]['success'] is False
        assert '재실행하지 않습니다' in http.reports[0][1]['detail']


@pytest.mark.parametrize('outcome', [result(False, 'Goal rejected'), result(False, 'Canceled'),
                                    result(False, 'Aborted')])
def test_unsuccessful_ros_result_is_reported_as_failure(tmp_path, outcome):
    http, robot = HTTP(command()), Robot(outcome)
    with Journal(tmp_path / 'bridge.sqlite3') as journal:
        Bridge(http, robot, journal).step()
    assert http.reports[0][1] == outcome


def test_backend_exception_becomes_failure_and_not_retried(tmp_path):
    http, robot = HTTP(command()), Robot()

    def execute(*_):
        robot.calls.append('started')
        raise TimeoutError('No result received')

    robot.execute = execute
    with Journal(tmp_path / 'bridge.sqlite3') as journal:
        bridge = Bridge(http, robot, journal)
        bridge.step()
        bridge.step()
    assert robot.calls == ['started']
    assert http.reports[0][1]['success'] is False
    assert 'TimeoutError' in http.reports[0][1]['detail']


@pytest.mark.parametrize('change', [
    {'device': 'motor'}, {'action': 'initialize'}, {'payload': {'quantity': 2}},
    {'payload': {'quantity': True}}, {'_bridge_deadline': None},
])
def test_bad_command_never_reaches_robot(tmp_path, change):
    http, robot = HTTP(dict(command(), **change)), Robot()
    with Journal(tmp_path / 'bridge.sqlite3') as journal:
        Bridge(http, robot, journal).step()
    assert robot.calls == []
    assert http.reports[0][1]['success'] is False


def test_journal_lock_and_conflicting_command_identity(tmp_path):
    path = tmp_path / 'bridge.sqlite3'
    cmd = command()
    with Journal(path) as journal:
        with pytest.raises(RuntimeError, match='이미 실행'):
            Journal(path)
        http, robot = HTTP(cmd), Robot()
        bridge = Bridge(http, robot, journal)
        bridge.step()
        http.cmd = dict(cmd, payload={'item_id': 'ITEM-002', 'quantity': 1})
        with pytest.raises(RuntimeError, match='내용이 변경'):
            bridge.step()
        assert len(robot.calls) == 1


def test_goal_file_validation(tmp_path):
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / 'config/mirobot_goal.example.json'
    goal = load_goal(source)
    assert goal['approach_height'] == 30.0
    path = tmp_path / 'goal.json'
    for value in (True, float('nan'), float('inf'), '100'):
        path.write_text(json.dumps(dict(goal, pick_x=value)))
        with pytest.raises(ValueError):
            load_goal(path)
    for changes in ({'approach_height': 0}, {'start': True}):
        path.write_text(json.dumps(dict(goal, **changes)))
        with pytest.raises(ValueError):
            load_goal(path)


@pytest.mark.parametrize('url', ['http://robot/orders/app-inventor', 'http://robot?query=x',
                                 'http://name:secret@robot', 'robot.local:8000', 'file:///tmp'])
def test_bridge_rejects_non_base_urls(url):
    with pytest.raises(ValueError):
        WarehouseHTTP(url, 'test')


def test_device_key_loading_and_result_limit(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    path.write_text("export DEVICE_KEY='file-key' # comment\n")
    monkeypatch.delenv('DEVICE_KEY', raising=False)
    assert load_device_key(path) == 'file-key'
    monkeypatch.setenv('DEVICE_KEY', 'environment-key')
    assert load_device_key(path) == 'environment-key'
    assert len(result(False, 'x' * 600)['detail']) == 500


def test_http_deadline_uses_remaining_time_without_shared_wall_clock(monkeypatch):
    http = WarehouseHTTP('http://robot.local:8000', 'key')
    monkeypatch.setattr(time, 'monotonic', lambda: 10.0)
    monkeypatch.setattr(http, 'request', lambda *_: {
        'command': {'id': 'test', 'expires_at': -100000.0, 'expires_in': 30.0}})
    assert http.next_command()['_bridge_deadline'] == 40.0
