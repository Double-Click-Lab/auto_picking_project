from tools.signal_monitor import SignalMonitor


def snapshot(instance='one', cursor=1, events=None):
    return {'instance': instance, 'cursor': cursor, 'oldest_id': 1,
            'events': events or [], 'mode': 'simulation', 'fault': None,
            'worker_alive': True, 'orders': []}


def test_monitor_quiet_wait_and_restart():
    event = {'id': 1, 'time': '2026-11-10T09:00:00+00:00',
             'kind': 'waiting_box', 'message': '상자 준비 신호 대기'}

    class Client:
        url = 'http://test'

        def __init__(self):
            self.paths = []
            self.responses = iter([snapshot(events=[event]), snapshot(),
                                   snapshot('two', 0), snapshot('two', 1, [event])])

        def request(self, method, path):
            assert method == 'GET'
            self.paths.append(path)
            return next(self.responses)

    client = Client()
    lines = []
    monitor = SignalMonitor(client, lines.append)
    monitor.poll()
    count = len(lines)
    monitor.poll()
    assert len(lines) == count
    monitor.poll()
    assert any('재시작' in line for line in lines)
    assert client.paths == ['/monitor/events?after=0', '/monitor/events?after=1',
                            '/monitor/events?after=1', '/monitor/events?after=0']
