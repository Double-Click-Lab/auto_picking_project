import asyncio
import sqlite3
from contextlib import asynccontextmanager, suppress

import httpx
import pytest

from app.main import create_app


HEADERS = {'X-API-Key': 'admin-test-key'}


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv('API_KEY', HEADERS['X-API-Key'])
    monkeypatch.setenv('CLEAR_ORDERS_ON_START', 'false')
    monkeypatch.setenv('REQUIRE_BOX_CONFIRMATION', 'true')
    path = tmp_path / 'start.sqlite3'
    return create_app(path, True), path


@asynccontextmanager
async def client_for(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test', headers=HEADERS) as client:
            yield client


async def submit_waiting(client, count=1):
    body = {'request_id': 'phone-batch', 'orders': [
        [f'customer-{index}', {'A': 1}, {'B': 0}, {'C': 0}, {'D': 0}]
        for index in range(count)]}
    response = await client.post('/orders/app-inventor', json=body)
    assert response.status_code == 202
    assert all(order['status'] == 'waiting' for order in response.json()['orders'])
    return body, [order['order_id'] for order in response.json()['orders']]


async def wait_for(client, order_id, expected):
    for _ in range(100):
        order = (await client.get(f'/orders/{order_id}')).json()
        if order['status'] == expected:
            return order
        await asyncio.sleep(0.05)
    raise AssertionError(order)


def test_waiting_is_idle_visible_and_preserved_on_restart(server):
    app, _ = server

    async def check():
        async with client_for(app) as client:
            body, ids = await submit_waiting(client, 2)
            # Several worker ticks must leave these orders and devices idle.
            await asyncio.sleep(0.4)
            assert (await client.get('/health')).json()['worker_alive'] is True
            for order_id in ids:
                order = (await client.get(f'/orders/{order_id}')).json()
                assert order['status'] == 'waiting'
                assert order['progress'] == {'status_text': '작업 시작 대기', 'events': []}
                assert order['commands'] == []
                assert order['box_replacement_required'] is False
            listed = (await client.get('/orders?limit=100')).json()['orders']
            assert {order['order_id'] for order in listed} == set(ids)
            assert all(order['status'] == 'waiting' for order in listed)
            assert {order['customer_id'] for order in listed} == {'customer-0', 'customer-1'}
            assert all(order['items'] == [{'item_id': 'ITEM-001', 'quantity': 1}] for order in listed)
            config = (await client.get('/configuration')).json()
            assert (await client.put('/configuration', json=config)).status_code == 409
            retry = await client.post('/orders/app-inventor', json=body)
            assert retry.status_code == 202
            assert [order['order_id'] for order in retry.json()['orders']] == ids
            assert all(order['status'] == 'waiting' for order in retry.json()['orders'])
        async with client_for(app) as client:
            await asyncio.sleep(0.2)
            for order_id in ids:
                assert (await client.get(f'/orders/{order_id}')).json()['status'] == 'waiting'
            assert (await client.post(f'/orders/{ids[1]}/start')).status_code == 200

    asyncio.run(check())


def test_selected_order_starts_idempotently_and_box_confirmation_unlocks_next(server):
    app, _ = server

    async def check():
        async with client_for(app) as client:
            body, (first, second) = await submit_waiting(client, 2)
            # An administrator may choose a later order first.
            started = await client.post(f'/orders/{second}/start')
            assert started.status_code == 200
            assert started.json() == {'order_id': second, 'status': 'queued', 'accepted': True}
            retry = await client.post(f'/orders/{second}/start')
            assert retry.status_code == 200
            assert retry.json()['accepted'] is True
            await wait_for(client, second, 'running')
            running_retry = await client.post(f'/orders/{second}/start')
            assert running_retry.status_code == 200
            assert running_retry.json() == {'order_id': second, 'status': 'running', 'accepted': True}
            retry_batch = (await client.post('/orders/app-inventor', json=body)).json()['orders']
            assert [order['status'] for order in retry_batch] == ['waiting', 'running']
            assert (await client.post(f'/orders/{first}/start')).status_code == 409
            order = await wait_for(client, second, 'waiting_box')
            assert order['box_replacement_required'] is True
            assert [cmd['device'] for cmd in order['commands']] == ['motor', 'camera', 'robot']
            assert (await client.post(f'/orders/{first}/start')).status_code == 409
            assert (await client.post(f'/orders/{second}/start')).status_code == 409
            assert (await client.post(f'/orders/{second}/box-ready')).status_code == 200
            assert (await client.post(f'/orders/{second}/box-ready')).status_code == 200
            assert (await client.post(f'/orders/{second}/start')).status_code == 409
            await asyncio.sleep(0.2)
            assert (await client.get(f'/orders/{first}')).json()['status'] == 'waiting'
            assert (await client.post(f'/orders/{first}/start')).status_code == 200
            await wait_for(client, first, 'waiting_box')
            assert (await client.post(f'/orders/{first}/box-ready')).status_code == 200

    asyncio.run(check())


@pytest.mark.parametrize('active_status', ['queued', 'running', 'waiting_box'])
def test_other_active_order_blocks_start(server, active_status):
    app, path = server

    async def check():
        async with client_for(app) as client:
            _, (target, other) = await submit_waiting(client, 2)
            with sqlite3.connect(path) as db:
                db.execute('UPDATE orders SET status=? WHERE id=?', (active_status, other))
            response = await client.post(f'/orders/{target}/start')
            assert response.status_code == 409
            assert 'detail' in response.json()
            assert (await client.get(f'/orders/{target}')).json()['status'] == 'waiting'

    asyncio.run(check())


@pytest.mark.parametrize('status', ['completed', 'failed', 'waiting_box'])
def test_non_waiting_target_and_missing_order_are_rejected(server, status):
    app, path = server

    async def check():
        async with client_for(app) as client:
            _, (order_id,) = await submit_waiting(client)
            assert (await client.post('/orders/missing/start')).status_code == 404
            with sqlite3.connect(path) as db:
                db.execute('UPDATE orders SET status=? WHERE id=?', (status, order_id))
            assert (await client.post(f'/orders/{order_id}/start')).status_code == 409

    asyncio.run(check())


@pytest.mark.parametrize('unavailable', ['fault', 'worker_stopped'])
def test_start_rejected_when_system_is_unavailable(server, unavailable):
    app, path = server

    async def check():
        async with client_for(app) as client:
            _, (order_id,) = await submit_waiting(client)
            if unavailable == 'fault':
                with sqlite3.connect(path) as db:
                    db.execute("UPDATE system SET fault='test device fault'")
            else:
                app.state.worker.cancel()
                with suppress(asyncio.CancelledError):
                    await app.state.worker
            response = await client.post(f'/orders/{order_id}/start')
            assert response.status_code == 503
            assert 'detail' in response.json()
            assert (await client.get(f'/orders/{order_id}')).json()['status'] == 'waiting'

    asyncio.run(check())


def test_start_requires_api_key(server):
    app, _ = server

    async def check():
        async with client_for(app) as client:
            _, (order_id,) = await submit_waiting(client)
            client.headers.clear()
            assert (await client.post(f'/orders/{order_id}/start')).status_code == 401
            assert (await client.post(f'/orders/{order_id}/start',
                                      headers={'X-API-Key': 'wrong'})).status_code == 401
            client.headers.update(HEADERS)
            assert (await client.get(f'/orders/{order_id}')).json()['status'] == 'waiting'
            assert (await client.post(f'/orders/{order_id}/start')).status_code == 200

    asyncio.run(check())


def test_simultaneous_start_requests_accept_only_one_order(server):
    app, _ = server

    async def check():
        async with client_for(app) as client:
            _, ids = await submit_waiting(client, 2)
            responses = await asyncio.gather(*(client.post(f'/orders/{order_id}/start') for order_id in ids))
            assert sorted(response.status_code for response in responses) == [200, 409]
            orders = (await client.get('/orders')).json()['orders']
            assert sum(order['status'] == 'waiting' for order in orders) == 1

    asyncio.run(check())


@pytest.mark.parametrize('endpoint', ['/orders', '/orders/batch'])
def test_legacy_order_apis_still_enqueue_and_run_automatically(server, endpoint):
    app, _ = server

    async def check():
        async with client_for(app) as client:
            _, (waiting_id,) = await submit_waiting(client)
            order = {'request_id': 'legacy', 'customer_id': 'legacy-customer',
                     'items': [{'item_id': 'ITEM-001', 'quantity': 1}]}
            body = order if endpoint == '/orders' else {'orders': [order]}
            response = await client.post(endpoint, json=body)
            assert response.status_code == 202
            accepted = response.json() if endpoint == '/orders' else response.json()['orders'][0]
            assert accepted['status'] == 'queued'
            assert (await client.post(f'/orders/{waiting_id}/start')).status_code == 409
            await wait_for(client, accepted['order_id'], 'waiting_box')
            assert (await client.get(f'/orders/{waiting_id}')).json()['status'] == 'waiting'
            assert (await client.post(f'/orders/{accepted["order_id"]}/box-ready')).status_code == 200

    asyncio.run(check())
