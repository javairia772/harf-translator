"""Validated human feedback and explicit opt-in example encryption."""
import json
import os
from typing import Literal
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, model_validator


FeedbackCategory = Literal["meaning", "terminology", "name", "number", "formatting", "fluency", "other"]


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: UUID
    rating: Literal["good", "corrected", "unusable"]
    categories: list[FeedbackCategory] = Field(default_factory=list, max_length=7)
    was_edited: bool = False
    correction_seconds: int | None = Field(default=None, ge=0, le=86400)
    consent_to_store_text: bool = False
    source_text: str | None = Field(default=None, max_length=5000)
    original_translation: str | None = Field(default=None, max_length=30000)
    corrected_translation: str | None = Field(default=None, max_length=30000)

    @model_validator(mode="after")
    def valid_feedback(self):
        if self.rating != "good" and not self.categories:
            raise ValueError("Choose at least one problem category.")
        texts = (self.source_text, self.original_translation, self.corrected_translation)
        if any(value is not None for value in texts) and not self.consent_to_store_text:
            raise ValueError("Text may only be sent with explicit consent.")
        if self.consent_to_store_text and not all(value and value.strip() for value in texts):
            raise ValueError("A consented example needs source, original and reviewed translation text.")
        return self


def encrypt_example(body: FeedbackRequest) -> str | None:
    if not body.consent_to_store_text:
        return None
    key = os.getenv("FEEDBACK_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("feedback_encryption_not_configured")
    try:
        fernet = Fernet(key.encode())
    except (ValueError, TypeError):
        raise RuntimeError("feedback_encryption_invalid") from None
    payload = json.dumps({
        "source_text": body.source_text,
        "original_translation": body.original_translation,
        "corrected_translation": body.corrected_translation,
    }, ensure_ascii=False, separators=(",", ":")).encode()
    return fernet.encrypt(payload).decode()


def decrypt_example(token: str, key: str) -> dict:  # Used only by offline, authorized evaluation tooling.
    try:
        return json.loads(Fernet(key.encode()).decrypt(token.encode()))
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("Cannot decrypt feedback example") from None
