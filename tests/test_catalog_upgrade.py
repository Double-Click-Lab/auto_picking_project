import asyncio
import json
import sqlite3

import httpx

from app.main import create_app


def test_old_simulation_catalog_adds_d_once(tmp_path, monkeypatch):
    monkeypatch.delenv('API_KEY', raising=False)
    path = tmp_path / 'old.sqlite3'
    original = {'motor_length_mm': 900, 'items': {
        f'ITEM-00{i}': {'name': f'기존 {i}', 'bin': f'B{i}', 'position_mm': i * 150}
        for i in range(1, 4)}}
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE configuration (body TEXT NOT NULL)')
        db.execute('INSERT INTO configuration VALUES (?)', (json.dumps(original),))

    async def check():
        app = create_app(path, True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                config = (await client.get('/configuration')).json()
                assert config['motor_length_mm'] == 900
                assert {k: config['items'][k] for k in original['items']} == original['items']
                assert config['items']['ITEM-004']['name'] == 'D'
                assert 'ITEM-004' in (await client.get('/items')).json()
                response = await client.post('/orders/app-inventor', json={
                    'request_id': 'upgrade-d', 'orders': [
                        ['person-1', {'A': 0}, {'B': 0}, {'C': 0}, {'D': 1}]]})
                assert response.status_code == 202
                order_id = response.json()['orders'][0]['order_id']
                assert (await client.post(f'/orders/{order_id}/start')).status_code == 200
                for _ in range(100):
                    order = (await client.get('/orders/' + order_id)).json()
                    if order['status'] == 'completed':
                        break
                    await asyncio.sleep(0.05)
                assert order['status'] == 'completed'
                assert order['items'] == [{'item_id': 'ITEM-004', 'quantity': 1}]
                assert [c['device'] for c in order['commands']] == ['motor', 'camera', 'robot']
                del config['items']['ITEM-004']
                assert (await client.put('/configuration', json=config)).status_code == 200
        app = create_app(path, True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                assert 'ITEM-004' not in (await client.get('/items')).json()

    asyncio.run(check())


def test_upgrade_preserves_real_or_short_motor_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv('API_KEY', 'test')
    monkeypatch.setenv('DEVICE_KEY', 'device-test')

    async def check():
        for simulation, length in [(False, 1000), (True, 600)]:
            path = tmp_path / f'{simulation}.sqlite3'
            original = {'motor_length_mm': length, 'items': {
                f'ITEM-00{i}': {'name': str(i), 'bin': str(i), 'position_mm': i * 100}
                for i in range(1, 4)}}
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE configuration (body TEXT NOT NULL)')
                db.execute('INSERT INTO configuration VALUES (?)', (json.dumps(original),))
            app = create_app(path, simulation)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                             headers={'X-API-Key': 'test'}) as client:
                    assert (await client.get('/configuration')).json() == original

    asyncio.run(check())
