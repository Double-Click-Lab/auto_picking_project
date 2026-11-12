import time

from fastapi.testclient import TestClient

from app.main import create_app


def wait_for(client, order_id, wanted):
    for _ in range(100):
        data = client.get('/orders/' + order_id).json()
        if data['status'] == wanted:
            return data
        time.sleep(0.05)
    raise AssertionError(data)


def test_simulation_and_idempotency(tmp_path):
    with TestClient(create_app(tmp_path / 'test.sqlite3', True)) as client:
        body = {'customer_id': 'customer-1', 'request_id': 'a', 'items': [
            {'item_id': 'ITEM-001', 'quantity': 2},
            {'item_id': 'ITEM-002', 'quantity': 1}]}
        result = client.post('/orders', json=body)
        assert result.status_code == 202
        oid = result.json()['order_id']
        assert client.post('/orders', json=body).json()['order_id'] == oid
        body['items'][0]['quantity'] = 3
        assert client.post('/orders', json=body).status_code == 409
        data = wait_for(client, oid, 'completed')
        assert data['progress']['status_text'] == '출고 완료'
        assert data['progress']['events'][-1]['message'] == '[모의 실행] customer-1 주문자의 물품을 출고시켰습니다.'
        assert all(e['simulation'] for e in data['progress']['events'])
        assert [c['device'] for c in data['commands']] == [
            'motor', 'camera', 'robot', 'robot', 'motor', 'camera', 'robot']


def test_validation(tmp_path):
    with TestClient(create_app(tmp_path / 'test.sqlite3', True)) as client:
        for item, quantity in [('unknown', 1), ('ITEM-001', 0), ('ITEM-001', True), ('ITEM-001', 1.5)]:
            assert client.post('/orders', json={'customer_id': 'customer-1', 'request_id': 'a', 'items': [
                {'item_id': item, 'quantity': quantity}]}).status_code == 422


def test_real_failure_blocks_following_orders(tmp_path, monkeypatch):
    monkeypatch.setenv('API_KEY', 'app-secret')
    monkeypatch.setenv('DEVICE_KEY', 'device-secret')
    with TestClient(create_app(tmp_path / 'test.sqlite3', False)) as client:
        assert client.get('/items').status_code == 401
        client.headers['X-API-Key'] = 'app-secret'
        body = {'customer_id': 'customer-1', 'request_id': 'a', 'items': [{'item_id': 'ITEM-001', 'quantity': 1}]}
        oid = client.post('/orders', json=body).json()['order_id']
        for _ in range(50):
            response = client.get('/devices/motor/commands/next', headers={'X-Device-Key': 'device-secret'})
            command = response.json()['command']
            if command:
                break
            time.sleep(0.05)
        assert command
        assert client.get('/devices/robot/commands/next', headers={'X-Device-Key': 'device-secret'}).json()['command'] is None
        url = '/devices/motor/commands/' + command['id'] + '/complete'
        headers = {'X-Device-Key': 'device-secret'}
        assert client.post(url, headers=headers, json={'success': False, 'detail': 'jam'}).status_code == 200
        assert client.post(url, headers=headers, json={'success': False, 'detail': 'jam'}).status_code == 200
        assert wait_for(client, oid, 'failed')['error'] == 'jam'
        body['request_id'] = 'b'
        assert client.post('/orders', json=body).status_code == 503


def test_restart_does_not_repeat_physical_work(tmp_path, monkeypatch):
    monkeypatch.setenv('API_KEY', 'key')
    monkeypatch.setenv('DEVICE_KEY', 'key')
    path = tmp_path / 'test.sqlite3'
    with TestClient(create_app(path, False)) as client:
        client.headers['X-API-Key'] = 'key'
        oid = client.post('/orders', json={'customer_id': 'customer-1', 'request_id': 'a', 'items': [
            {'item_id': 'ITEM-001', 'quantity': 1}]}).json()['order_id']
        wait_for(client, oid, 'running')
    with TestClient(create_app(path, False)) as client:
        client.headers['X-API-Key'] = 'key'
        assert client.get('/orders/' + oid).json()['status'] == 'failed'
        assert client.get('/health').json()['fault']


def test_timeout_and_reset(tmp_path, monkeypatch):
    monkeypatch.setenv('API_KEY', 'key')
    monkeypatch.setenv('DEVICE_KEY', 'key')
    monkeypatch.setenv('COMMAND_TIMEOUT', '0.1')
    with TestClient(create_app(tmp_path / 'test.sqlite3', False)) as client:
        client.headers['X-API-Key'] = 'key'
        oid = client.post('/orders', json={'customer_id': 'customer-1', 'request_id': 'a', 'items': [
            {'item_id': 'ITEM-001', 'quantity': 1}]}).json()['order_id']
        data = wait_for(client, oid, 'failed')
        assert len(data['commands']) == 1
        cmd = data['commands'][0]
        assert client.post('/devices/motor/commands/' + cmd['id'] + '/complete',
                           headers={'X-Device-Key': 'key'}, json={'success': True}).status_code == 409
        assert client.post('/system/reset').status_code == 200
        assert client.get('/health').json()['fault'] is None


def test_real_success_requires_every_completion(tmp_path, monkeypatch):
    monkeypatch.setenv('API_KEY', 'key')
    monkeypatch.setenv('DEVICE_KEY', 'key')
    with TestClient(create_app(tmp_path / 'test.sqlite3', False)) as client:
        client.headers.update({'X-API-Key': 'key', 'X-Device-Key': 'key'})
        oid = client.post('/orders', json={'customer_id': 'customer-1', 'request_id': 'a', 'items': [
            {'item_id': 'ITEM-001', 'quantity': 1}]}).json()['order_id']
        for device in ['motor', 'camera', 'robot']:
            command = None
            for _ in range(50):
                command = client.get(f'/devices/{device}/commands/next').json()['command']
                if command:
                    break
                time.sleep(0.05)
            assert command
            progress = client.get('/orders/' + oid).json()['progress']['events']
            current = [e for e in progress if e.get('command_id') == command['id']]
            assert [e['kind'] for e in current] == ['prepared', 'sent']
            assert not any(e['simulation'] for e in current)
            time.sleep(0.15)
            assert client.get('/orders/' + oid).json()['status'] == 'running'
            url = f'/devices/{device}/commands/{command["id"]}/complete'
            assert client.post(url, json={'success': True}).status_code == 200
            assert client.post(url, json={'success': True}).status_code == 200
            assert client.post(url, json={'success': False}).status_code == 409
        wait_for(client, oid, 'completed')


def test_batch_customers_fifo_and_atomic_validation(tmp_path):
    import sqlite3
    path = tmp_path / 'batch.sqlite3'
    orders = [
        {'request_id': 'first', 'customer_id': 'person-1', 'items': [
            {'item_id': 'ITEM-001', 'quantity': 2},
            {'item_id': 'ITEM-002', 'quantity': 1},
            {'item_id': 'ITEM-003', 'quantity': 3}]},
        {'request_id': 'second', 'customer_id': 'person-2', 'items': [
            {'item_id': 'ITEM-002', 'quantity': 3},
            {'item_id': 'ITEM-003', 'quantity': 1}]},
    ]
    with TestClient(create_app(path, True)) as client:
        invalid = {'request_id': 'invalid', 'customer_id': 'person-3',
                   'items': [{'item_id': 'unknown', 'quantity': 1}]}
        assert client.post('/orders/batch', json={'orders': [orders[0], invalid]}).status_code == 422
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0
        response = client.post('/orders/batch', json={'orders': orders})
        assert response.status_code == 202
        ids = [o['order_id'] for o in response.json()['orders']]
        assert client.post('/orders/batch', json={'orders': orders}).json() == response.json()
        config = client.get('/configuration').json()
        assert client.put('/configuration', json=config).status_code == 409
        wait_for(client, ids[1], 'completed')
        assert client.get('/orders/' + ids[0]).json()['status'] == 'completed'
        with sqlite3.connect(path) as db:
            commands = db.execute('SELECT order_id, device, payload FROM commands ORDER BY rowid').fetchall()
        sequence = [c[0] for c in commands]
        assert sequence == sorted(sequence, key=ids.index)
        import json
        for oid, expected_customer, expected_picks in zip(ids, ['person-1', 'person-2'], [6, 4]):
            payloads = [json.loads(c[2]) for c in commands if c[0] == oid and c[1] == 'robot']
            assert len(payloads) == expected_picks
            assert all(p['customer_id'] == expected_customer and p['destination_id'] == oid for p in payloads)
        assert all(json.loads(c[2])['item_id'] != 'ITEM-001' for c in commands if c[0] == ids[1])
        changed = dict(orders[0], customer_id='another-person')
        assert client.post('/orders', json=changed).status_code == 409


def test_dynamic_configuration_persists_and_validates_travel(tmp_path):
    import json
    path = tmp_path / 'config.sqlite3'
    config = {'motor_length_mm': 2500, 'items': {
        'NEW': {'name': '새 품목', 'bin': 'Z9', 'position_mm': 2300}}}
    with TestClient(create_app(path, True)) as client:
        assert client.put('/configuration', json=dict(config, motor_length_mm=2000)).status_code == 422
        assert client.put('/configuration', json=config).status_code == 200
        assert list(client.get('/items').json()) == ['NEW']
    with TestClient(create_app(path, True)) as client:
        assert client.get('/configuration').json() == config
        oid = client.post('/orders', json={'customer_id': 'new-customer', 'request_id': 'new',
            'items': [{'item_id': 'NEW', 'quantity': 1}]}).json()['order_id']
        data = wait_for(client, oid, 'completed')
        payload = json.loads(data['commands'][0]['payload'])
        assert payload['motor_length_mm'] == 2500
        assert payload['position_mm'] == 2300


def test_saved_order_list_after_restart(tmp_path):
    path = tmp_path / 'list.sqlite3'
    with TestClient(create_app(path, True)) as client:
        oid = client.post('/orders', json={'customer_id': 'person-1', 'request_id': 'saved',
            'items': [{'item_id': 'ITEM-001', 'quantity': 1}]}).json()['order_id']
        wait_for(client, oid, 'completed')
    with TestClient(create_app(path, True)) as client:
        result = client.get('/orders?customer_id=person-1').json()
        assert result['total'] == 1
        assert result['orders'][0]['order_id'] == oid
        assert result['orders'][0]['items'][0]['quantity'] == 1
        assert client.get('/orders?customer_id=missing').json()['total'] == 0
        assert client.get('/orders?offset=1').json()['orders'] == []
        assert client.get('/orders?limit=0').status_code == 422


def test_app_inventor_batch(tmp_path):
    import copy
    import sqlite3
    path = tmp_path / 'inventor.sqlite3'
    body = {'request_id': 'phone-batch-1', 'orders': [
        ['person-1', {'A': 2}, {'B': 0}, {'C': 0}, {'D': 1}],
        ['person-2', {'A': 0}, {'B': 1}, {'C': 0}, {'D': 0}]]}
    with TestClient(create_app(path, True)) as client:
        response = client.post('/orders/app-inventor', json=body)
        assert response.status_code == 202, response.text
        ids = [o['order_id'] for o in response.json()['orders']]
        assert [o['order_id'] for o in client.post('/orders/app-inventor', json=body).json()['orders']] == ids
        changed = copy.deepcopy(body)
        changed['orders'].pop()
        assert client.post('/orders/app-inventor', json=changed).status_code == 409
        assert all(order['status'] == 'waiting' for order in response.json()['orders'])
        for order_id in ids:
            assert client.post(f'/orders/{order_id}/start').status_code == 200
            wait_for(client, order_id, 'completed')
        first = client.get('/orders/' + ids[0]).json()
        assert first['status'] == 'completed'
        assert first['items'] == [{'item_id': 'ITEM-001', 'quantity': 2}, {'item_id': 'ITEM-004', 'quantity': 1}]
        assert [c['device'] for c in first['commands']] == ['motor', 'camera', 'robot', 'robot', 'motor', 'camera', 'robot']
        for value in [-1, 1.5, True, '2']:
            invalid = copy.deepcopy(body)
            invalid['request_id'] = 'invalid'
            invalid['orders'][1][2]['B'] = value
            assert client.post('/orders/app-inventor', json=invalid).status_code == 422
        for row in [
            ['empty', {'A': 0}, {'B': 0}, {'C': 0}, {'D': 0}],
            ['wrong', {'B': 1}, {'A': 1}, {'C': 0}, {'D': 0}],
            ['short', {'A': 1}],
        ]:
            invalid = {'request_id': 'invalid', 'orders': [body['orders'][0], row]}
            assert client.post('/orders/app-inventor', json=invalid).status_code == 422
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 2
        config = client.get('/configuration').json()
        del config['items']['ITEM-004']
        assert client.put('/configuration', json=config).status_code == 200
        invalid = dict(body, request_id='missing-product')
        assert client.post('/orders/app-inventor', json=invalid).status_code == 422
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 2
            assert db.execute('SELECT COUNT(*) FROM app_inventor_requests').fetchone()[0] == 1
