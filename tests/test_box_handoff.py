import asyncio

import httpx

from app.main import create_app


def test_box_handoff_blocks_fifo_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('REQUIRE_BOX_CONFIRMATION', 'true')
    monkeypatch.setenv('CLEAR_ORDERS_ON_START', 'false')
    monkeypatch.delenv('API_KEY', raising=False)
    path = tmp_path / 'handoff.sqlite3'

    async def check():
        app = create_app(path, True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                # The legacy batch API retains automatic FIFO processing.
                batch = {'orders': [
                    {'request_id': customer, 'customer_id': customer,
                     'items': [{'item_id': 'ITEM-001', 'quantity': 1}]}
                    for customer in ('first', 'second')]}
                response = await client.post('/orders/batch', json=batch)
                assert response.status_code == 202
                first, second = [r['order_id'] for r in response.json()['orders']]
                assert (await client.post(f'/orders/{second}/box-ready')).status_code == 409
                for _ in range(100):
                    order = (await client.get('/orders/' + first)).json()
                    if order['status'] == 'waiting_box':
                        break
                    await asyncio.sleep(0.05)
                assert order['status'] == 'waiting_box'
                assert order['box_replacement_required'] is True
                await asyncio.sleep(0.3)
                assert (await client.get('/orders/' + second)).json()['status'] == 'queued'
                snapshot = (await client.get('/monitor/events')).json()
                assert [e['customer_id'] for e in snapshot['events'] if e['kind'] == 'order_received'] == ['first', 'second']
                assert any(e['kind'] == 'waiting_box' for e in snapshot['events'])
                assert not any(e['kind'] == 'order_started' and e['order_id'] == second for e in snapshot['events'])
                quiet = (await client.get('/monitor/events', params={'after': snapshot['cursor']})).json()
                assert quiet['events'] == []
                assert quiet['cursor'] == snapshot['cursor']

        app = create_app(path, True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                assert (await client.get('/orders/' + first)).json()['status'] == 'waiting_box'
                assert (await client.get('/orders/' + second)).json()['commands'] == []
                assert (await client.post(f'/orders/{first}/box-ready')).status_code == 200
                assert (await client.post(f'/orders/{first}/box-ready')).status_code == 200
                for _ in range(100):
                    order = (await client.get('/orders/' + second)).json()
                    if order['status'] == 'waiting_box':
                        break
                    await asyncio.sleep(0.05)
                assert order['status'] == 'waiting_box'
                events = (await client.get('/monitor/events')).json()['events']
                ready = next(e['id'] for e in events if e['kind'] == 'box_ready' and e['order_id'] == first)
                started = next(e['id'] for e in events if e['kind'] == 'order_started' and e['order_id'] == second)
                assert ready < started
                assert len([e for e in events if e['kind'] == 'box_ready' and e['order_id'] == first]) == 1
                assert (await client.post(f'/orders/{first}/box-ready')).status_code == 200
                assert (await client.get('/orders/' + second)).json()['status'] == 'waiting_box'
                assert (await client.post(f'/orders/{second}/box-ready')).status_code == 200

    asyncio.run(check())
