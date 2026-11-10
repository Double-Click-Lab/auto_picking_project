import asyncio
import sqlite3

import httpx
import pytest

from app.main import create_app


@pytest.mark.parametrize('clear', [True, False])
def test_restart_cleanup_and_request_reuse(tmp_path, monkeypatch, clear):
    monkeypatch.setenv('CLEAR_ORDERS_ON_START', str(clear).lower())
    monkeypatch.delenv('API_KEY', raising=False)
    path = tmp_path / 'cleanup.sqlite3'
    body = {'request_id': 'same-request', 'orders': [
        ['same-customer', {'A': 0}, {'B': 0}, {'C': 0}, {'D': 1}]]}

    async def check():
        app = create_app(path, True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                config = (await client.get('/configuration')).json()
                config['items']['ITEM-004']['position_mm'] = 750
                assert (await client.put('/configuration', json=config)).status_code == 200
                response = await client.post('/orders/app-inventor', json=body)
                old_id = response.json()['orders'][0]['order_id']
                for _ in range(100):
                    order = (await client.get('/orders/' + old_id)).json()
                    if order['status'] == 'completed':
                        break
                    await asyncio.sleep(0.05)
                assert order['status'] == 'completed'
        app = create_app(path, True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                assert (await client.get('/orders')).json()['total'] == (0 if clear else 1)
                assert (await client.get('/configuration')).json() == config
                with sqlite3.connect(path) as db:
                    for table in ('commands', 'app_inventor_requests'):
                        count = db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                        assert (count == 0) == clear
                response = await client.post('/orders/app-inventor', json=body)
                assert response.status_code == 202
                new_id = response.json()['orders'][0]['order_id']
                assert (new_id != old_id) == clear
                # Within one run, retry protection remains active.
                assert (await client.post('/orders/app-inventor', json=body)).json()['orders'][0]['order_id'] == new_id

    asyncio.run(check())
