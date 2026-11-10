import asyncio

import httpx

from app.main import create_app


def test_access_history_auth_errors_and_limit(tmp_path, monkeypatch):
    monkeypatch.setenv('API_KEY', 'test-secret')

    async def check():
        app = create_app(tmp_path / 'history.sqlite3', True)
        transport = httpx.ASGITransport(app=app, client=('192.168.0.77', 12345))
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            assert (await client.get('/connections')).status_code == 401
            await client.post('/orders/app-inventor', json={})
            await client.post('/orders/app-inventor', json={}, headers={'X-API-Key': 'test-secret'})
            response = await client.get('/connections', headers={'X-API-Key': 'test-secret'})
            records = response.json()['requests']
            assert [r['status'] for r in records] == [422, 401]
            assert all(r['ip'] == '192.168.0.77' for r in records)
            assert 'test-secret' not in response.text
            for _ in range(205):
                await client.get('/missing?secret=hidden')
            response = await client.get('/connections', headers={'X-API-Key': 'test-secret'})
            assert len(response.json()['requests']) == 200
            assert 'hidden' not in response.text

    asyncio.run(check())
