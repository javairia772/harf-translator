import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.pilot import PilotGuard


def test_production_fails_closed(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('PILOT_PASSWORD', raising=False)
    with pytest.raises(RuntimeError):
        PilotGuard(FastAPI())


def test_pilot_auth_health_and_limits(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('PILOT_USERNAME', 'tester')
    monkeypatch.setenv('PILOT_PASSWORD', 'test-only-long-password')
    monkeypatch.setenv('ALLOWED_HOSTS', 'testserver')
    app = FastAPI()
    app.add_middleware(PilotGuard)
    @app.get('/healthz')
    async def health(): return {'status':'ok'}
    @app.post('/api/translate')
    async def translate(): return {'ok':True}
    with TestClient(app) as client:
        assert client.get('/healthz').status_code == 200
        assert client.post('/api/translate').status_code == 401
        assert client.post('/api/translate', auth=('tester','wrong')).status_code == 401
        for _ in range(30):
            assert client.post('/api/translate', auth=('tester','test-only-long-password')).status_code == 200
        assert client.post('/api/translate', auth=('tester','test-only-long-password')).status_code == 429
