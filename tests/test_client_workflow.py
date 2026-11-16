from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tools.test_client import Client


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    monkeypatch.setenv('API_KEY', 'workflow-key')
    monkeypatch.setenv('CLEAR_ORDERS_ON_START', 'false')
    monkeypatch.setenv('REQUIRE_BOX_CONFIRMATION', 'true')
    app = create_app(tmp_path / 'workflow.sqlite3', True)
    with TestClient(app, headers={'X-API-Key': 'workflow-key'}) as server:
        client = Client('http://test', 'workflow-key')
        calls = []

        def request(method, path, body=None):
            # Exercise real API state transitions and the background worker.
            response = server.request(method, path, json=body)
            calls.append((method, path, body))
            if response.is_error:
                raise RuntimeError(f'HTTP {response.status_code}: {response.text}')
            return response.json()

        with patch.object(client, 'request', side_effect=request):
            yield client, server, calls


def test_full_menu_completes_multiple_orders_and_prints_each_event_once(workflow, capsys):
    client, server, calls = workflow
    answers = iter(['9', 'first', '2', '0', '0', '1', '',
                    'second', '0', '1', '0', '0', 's', 'y', 'y', 'y', 'y', '0'])
    confirmations = []

    def answer(prompt):
        if '작업 시작:' in prompt or '새 상자 준비 완료:' in prompt:
            rows = server.get('/orders').json()['orders']
            by_customer = {row['customer_id']: row for row in rows}
            confirmations.append(prompt)
            if len(confirmations) == 1:
                assert all(row['status'] == 'waiting' for row in rows)
            elif len(confirmations) == 2:
                assert by_customer['first']['status'] == 'waiting_box'
                assert by_customer['second']['status'] == 'waiting'
            elif len(confirmations) == 3:
                assert by_customer['first']['status'] == 'completed'
                assert by_customer['second']['status'] == 'waiting'
            else:
                assert by_customer['second']['status'] == 'waiting_box'
        return next(answers)

    with patch('builtins.input', side_effect=answer):
        client.run()

    assert len(confirmations) == 4
    orders = server.get('/orders').json()['orders']
    assert all(order['status'] == 'completed' for order in orders)
    first, second = [order['order_id'] for order in client.history]
    assert [(method, path) for method, path, _ in calls if method == 'POST'] == [
        ('POST', '/orders/app-inventor'),
        ('POST', f'/orders/{first}/start'),
        ('POST', f'/orders/{first}/box-ready'),
        ('POST', f'/orders/{second}/start'),
        ('POST', f'/orders/{second}/box-ready'),
    ]
    detail = server.get(f'/orders/{first}').json()
    assert [command['device'] for command in detail['commands']] == [
        'motor', 'camera', 'robot', 'robot', 'motor', 'camera', 'robot']
    output = capsys.readouterr().out
    assert '전체 테스트 완료: 2명' in output
    for device in ('리니어 모터', '카메라', '로봇팔'):
        assert f'{device}에 신호를 주었습니다.' in output
    for customer in ('first', 'second'):
        assert output.count(f'{customer} 주문자의 물품을 출고시켰습니다.') == 1


def test_box_pause_can_resume_in_menu_four_without_restarting(workflow, capsys):
    client, server, calls = workflow
    answers = ['9', 'first', '1', '0', '0', '0', 's', 'y', 'b',
               '4', '1', 'y', '0']
    with patch('builtins.input', side_effect=answers):
        client.run()
    order_id = client.history[0]['order_id']
    assert server.get(f'/orders/{order_id}').json()['status'] == 'completed'
    paths = [path for method, path, _ in calls if method == 'POST']
    assert paths.count(f'/orders/{order_id}/start') == 1
    assert paths.count(f'/orders/{order_id}/box-ready') == 1
    assert '상자 교체 대기를 유지합니다.' in capsys.readouterr().out


def test_cancel_start_keeps_submitted_order_waiting(workflow):
    client, server, calls = workflow
    answers = ['9', 'first', '1', '0', '0', '0', 's', 'b', '0']
    with patch('builtins.input', side_effect=answers):
        client.run()
    order = server.get('/orders').json()['orders'][0]
    assert order['status'] == 'waiting'
    assert [path for method, path, _ in calls if method == 'POST'] == ['/orders/app-inventor']


def test_box_confirmation_is_not_sent_before_shipping(workflow):
    client, server, calls = workflow
    response = server.post('/orders/app-inventor', json={
        'request_id': 'waiting',
        'orders': [['first', {'A': 1}, {'B': 0}, {'C': 0}, {'D': 0}]],
    })
    order_id = response.json()['orders'][0]['order_id']
    with patch('builtins.input') as answer:
        assert client.confirm_box(order_id) is False
    answer.assert_not_called()
    assert not any(method == 'POST' for method, _, _ in calls)


def test_failure_stops_batch_without_starting_next_order(capsys):
    client = Client('http://test', '')
    orders = {'orders': [{'order_id': name, 'customer_id': name} for name in ('first', 'second')]}
    failed = {'customer_id': 'first', 'status': 'failed', 'items': [], 'error': 'motor fault',
              'progress': {'events': []}}
    with patch.object(client, 'request', return_value={'mode': 'simulation', 'fault': None,
                                                     'worker_alive': True}) as request, \
         patch.object(client, 'submit_app_inventor_orders', return_value=orders), \
         patch.object(client, 'get_order', return_value=failed) as get_order:
        client.full_test()
    assert all(call.args == ('first',) for call in get_order.call_args_list)
    request.assert_called_once_with('GET', '/health')
    assert '처리 실패: motor fault' in capsys.readouterr().out
