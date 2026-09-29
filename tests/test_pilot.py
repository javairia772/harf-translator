import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.pilot import PilotGuard


@pytest.fixture(autouse=True)
def private_default(monkeypatch):
    monkeypatch.delenv('ACCESS_MODE', raising=False)


def test_public_mode_retains_production_limits(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('ACCESS_MODE', 'public')
    monkeypatch.setenv('ALLOWED_HOSTS', 'testserver')
    monkeypatch.delenv('PILOT_USERNAME', raising=False)
    monkeypatch.delenv('PILOT_PASSWORD', raising=False)
    app = FastAPI()
    app.add_middleware(PilotGuard)
    @app.get('/')
    async def home(): return {'ok':True}
    @app.post('/api/translate')
    async def translate(): return {'ok':True}
    with TestClient(app) as client:
        response = client.get('/')
        assert response.status_code == 200
        assert 'www-authenticate' not in response.headers
        for _ in range(30):
            assert client.post('/api/translate').status_code == 200
        assert client.post('/api/translate').status_code == 429
        assert client.post('/api/translate',content=b'x'*262145).status_code == 413


def test_public_mode_still_requires_hosts(monkeypatch):
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.setenv('ACCESS_MODE','public')
    monkeypatch.delenv('ALLOWED_HOSTS',raising=False)
    with pytest.raises(RuntimeError,match='ALLOWED_HOSTS'):
        PilotGuard(FastAPI())


def test_invalid_access_mode_is_rejected(monkeypatch):
    monkeypatch.setenv('ACCESS_MODE','publc')
    with pytest.raises(RuntimeError,match='ACCESS_MODE'):
        PilotGuard(FastAPI())


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
