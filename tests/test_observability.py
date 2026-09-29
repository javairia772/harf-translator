import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main
from app.feedback import FeedbackRequest, decrypt_example, encrypt_example
from app.observability import TelemetryStore
from app.translation import ModelTranslation


def store_at(tmp_path):
    return TelemetryStore(f"sqlite:///{tmp_path / 'telemetry.db'}")



def test_environment_database_can_be_configured_after_store_creation(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = TelemetryStore()
    assert store.enabled is False
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'late.db'}")
    assert store.ensure_schema() is True
    assert store.enabled is True
def test_store_summarizes_workflows_without_document_text(tmp_path):
    store = store_at(tmp_path)
    assert store.ensure_schema()
    store.start_workflow(workflow_id="wf", request_id="req", session_hash="session",
        direction="en-ur", source_type="pdf", input_characters=120,
        prompt_version="v1", glossary_sha256="g" * 64, app_version="a" * 40)
    store.record_event(session_hash="session", event_type="translation_started", workflow_id="wf")
    store.finish_workflow("wf", outcome="success", elapsed_ms=800,
                          output_characters=100, warning_count=1, model="model")
    store.record_provider_summary("wf", {"attempts": 2, "error_codes": ["timeout"],
        "usage_per_attempt": [None, {"promptTokenCount": 20, "candidatesTokenCount": 10, "totalTokenCount": 30}]})
    store.save_feedback(workflow_id="wf", session_hash="session", rating="corrected",
        categories=["terminology"], was_edited=True, correction_seconds=20,
        consent=False, encrypted_example=None)
    summary = store.summary(24)
    assert summary["translations"] == 1
    assert summary["provider_attempts"] == 2
    assert summary["total_tokens_reported"] == 30
    assert summary["feedback"]["ratings"] == {"corrected": 1}
    database = (tmp_path / "telemetry.db").read_bytes()
    assert b"document secret" not in database


def test_feedback_text_requires_consent_and_is_encrypted(monkeypatch):
    with pytest.raises(ValidationError):
        FeedbackRequest(workflow_id="00000000-0000-0000-0000-000000000001", rating="good",
                        source_text="secret")
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("FEEDBACK_ENCRYPTION_KEY", key)
    body = FeedbackRequest(workflow_id="00000000-0000-0000-0000-000000000001",
        rating="corrected", categories=["meaning"], was_edited=True,
        consent_to_store_text=True, source_text="private source",
        original_translation="original", corrected_translation="correction")
    token = encrypt_example(body)
    assert "private source" not in token
    assert decrypt_example(token, key)["corrected_translation"] == "correction"


def test_api_records_translation_feedback_and_admin_summary(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    monkeypatch.setattr(main, "telemetry", store)
    monkeypatch.setenv("ADMIN_USERNAME", "developer")
    monkeypatch.setenv("ADMIN_PASSWORD", "a-secure-admin-password")

    class Provider:
        metadata = {"model": "test-model", "prompt_sha256": "p" * 64,
                    "attempts": 1, "retries": 0, "error_codes": [],
                    "usage_per_attempt": [{"totalTokenCount": 42}]}
        async def translate(self, request):
            return ModelTranslation(translation="درخواست منظور نہیں ہوئی۔", notes=[])

    main.app.dependency_overrides[main.get_provider] = Provider
    try:
        with TestClient(main.app) as client:
            translated = client.post("/api/translate", json={"text": "The application was not approved.",
                                      "direction": "en-ur", "source_type": "paste"})
            assert translated.status_code == 200
            workflow_id = translated.json()["workflow_id"]
            feedback = client.post("/api/feedback", json={"workflow_id": workflow_id,
                "rating": "good", "categories": [], "was_edited": False,
                "consent_to_store_text": False})
            assert feedback.status_code == 200
            assert client.get("/api/admin/summary").status_code == 401
            summary = client.get("/api/admin/summary", auth=("developer", "a-secure-admin-password"))
            assert summary.status_code == 200
            assert summary.json()["translations"] == 1
            assert summary.json()["feedback"]["ratings"] == {"good": 1}
    finally:
        main.app.dependency_overrides.clear()


def test_readiness_does_not_call_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "telemetry", store_at(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "configured")
    monkeypatch.setenv("GEMINI_MODEL", "model")
    with TestClient(main.app) as client:
        result = client.get("/readyz")
    assert result.status_code == 200
    assert result.json()["provider_configured"] is True
    assert result.json()["telemetry"]["reachable"] is True

