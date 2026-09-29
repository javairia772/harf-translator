"""Privacy-aware workflow telemetry for the pilot."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS workflows (
      workflow_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, session_hash TEXT NOT NULL,
      direction TEXT NOT NULL, source_type TEXT NOT NULL, input_characters INTEGER NOT NULL,
      output_characters INTEGER, warning_count INTEGER, outcome TEXT NOT NULL, error_code TEXT,
      elapsed_ms INTEGER, model TEXT, prompt_version TEXT, prompt_sha256 TEXT,
      glossary_sha256 TEXT, app_version TEXT, started_at TEXT NOT NULL, finished_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS events (
      event_id TEXT PRIMARY KEY, workflow_id TEXT, request_id TEXT, session_hash TEXT NOT NULL,
      event_type TEXT NOT NULL, status TEXT NOT NULL, error_code TEXT, duration_ms INTEGER,
      metadata_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS provider_attempts (
      attempt_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, attempt_number INTEGER NOT NULL,
      input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER, error_code TEXT,
      created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS feedback (
      feedback_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, session_hash TEXT NOT NULL,
      rating TEXT NOT NULL, categories_json TEXT NOT NULL, was_edited INTEGER NOT NULL,
      correction_seconds INTEGER, consent_to_store_text INTEGER NOT NULL,
      encrypted_example TEXT, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_hash)",
    "CREATE INDEX IF NOT EXISTS idx_workflows_started ON workflows(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_attempts_workflow ON provider_attempts(workflow_id)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at)",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def anonymous_session(raw_session: str) -> str:
    salt = os.getenv("SESSION_HASH_SALT", "harf-local-session-salt")
    return hashlib.sha256(f"{salt}:{raw_session}".encode()).hexdigest()[:32]


def _usage_value(usage: dict[str, Any] | None, *names: str) -> int | None:
    if isinstance(usage, dict):
        for name in names:
            value = usage.get(name)
            if isinstance(value, int) and value >= 0:
                return value
    return None


class TelemetryStore:
    """Small DB-API store. Failures are reported in readiness, not to end users."""
    def __init__(self, database_url: str | None = None):
        self.from_environment = database_url is None
        self.database_url = (database_url if database_url is not None else os.getenv("DATABASE_URL", "")).strip()
        self.enabled = bool(self.database_url)
        self.kind = "sqlite" if self.database_url.startswith("sqlite:///") else "postgres"
        self._ready = False
        self._lock = threading.Lock()
        self.last_error: str | None = None

    @property
    def placeholder(self) -> str:
        return "?" if self.kind == "sqlite" else "%s"

    def _connect(self):
        if self.kind == "sqlite":
            path = self.database_url.removeprefix("sqlite:///")
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            return sqlite3.connect(path, timeout=10)
        if psycopg is None:
            raise RuntimeError("psycopg is not installed")
        return psycopg.connect(self.database_url.replace("postgres://", "postgresql://", 1), connect_timeout=5)

    def ensure_schema(self) -> bool:
        if not self.enabled and self.from_environment:
            self.database_url = os.getenv("DATABASE_URL", "").strip()
            self.enabled = bool(self.database_url)
            self.kind = "sqlite" if self.database_url.startswith("sqlite:///") else "postgres"
        if not self.enabled:
            return False
        if self._ready:
            return True
        with self._lock:
            if self._ready:
                return True
            try:
                with self._connect() as connection:
                    for statement in SCHEMA:
                        connection.execute(statement)
                self._ready, self.last_error = True, None
            except Exception as error:
                self._ready, self.last_error = False, type(error).__name__
        return self._ready

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> bool:
        if not self.ensure_schema():
            return False
        try:
            with self._connect() as connection:
                connection.execute(sql, params)
            self.last_error = None
            return True
        except Exception as error:
            self.last_error = type(error).__name__
            return False

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple]:
        if not self.ensure_schema():
            return []
        try:
            with self._connect() as connection:
                return list(connection.execute(sql, params).fetchall())
        except Exception as error:
            self.last_error = type(error).__name__
            return []

    def record_event(self, *, session_hash: str, event_type: str, status: str = "ok",
                     workflow_id: str | None = None, request_id: str | None = None,
                     error_code: str | None = None, duration_ms: int | None = None,
                     metadata: dict[str, Any] | None = None) -> bool:
        p = self.placeholder
        return self._execute(f"INSERT INTO events VALUES ({','.join([p] * 10)})",
            (str(uuid.uuid4()), workflow_id, request_id, session_hash, event_type, status,
             error_code, duration_ms, json.dumps(metadata or {}, separators=(",", ":")), utc_now()))

    def start_workflow(self, *, workflow_id: str, request_id: str, session_hash: str,
                       direction: str, source_type: str, input_characters: int,
                       prompt_version: str, glossary_sha256: str, app_version: str) -> bool:
        p = self.placeholder
        values = (workflow_id, request_id, session_hash, direction, source_type,
                  input_characters, None, None, "started", None, None, None,
                  prompt_version, None, glossary_sha256, app_version, utc_now(), None)
        return self._execute(f"INSERT INTO workflows VALUES ({','.join([p] * 18)})", values)

    def finish_workflow(self, workflow_id: str, *, outcome: str, elapsed_ms: int,
                        output_characters: int | None = None, warning_count: int | None = None,
                        error_code: str | None = None, model: str | None = None,
                        prompt_sha256: str | None = None) -> bool:
        p = self.placeholder
        return self._execute(
            f"UPDATE workflows SET output_characters={p}, warning_count={p}, outcome={p}, error_code={p}, "
            f"elapsed_ms={p}, model={p}, prompt_sha256={p}, finished_at={p} WHERE workflow_id={p}",
            (output_characters, warning_count, outcome, error_code, elapsed_ms, model,
             prompt_sha256, utc_now(), workflow_id))

    def record_provider_summary(self, workflow_id: str, metadata: dict[str, Any]) -> None:
        usages, errors = metadata.get("usage_per_attempt") or [], metadata.get("error_codes") or []
        attempts = max(int(metadata.get("attempts") or 0), len(usages), len(errors))
        usage_start = max(0, attempts - len(usages))
        for index in range(attempts):
            usage = usages[index - usage_start] if index >= usage_start else None
            error = errors[index] if index < len(errors) else None
            p = self.placeholder
            self._execute(f"INSERT INTO provider_attempts VALUES ({','.join([p] * 8)})",
                (str(uuid.uuid4()), workflow_id, index + 1,
                 _usage_value(usage, "promptTokenCount", "inputTokenCount"),
                 _usage_value(usage, "candidatesTokenCount", "outputTokenCount"),
                 _usage_value(usage, "totalTokenCount"), error, utc_now()))

    def save_feedback(self, *, workflow_id: str, session_hash: str, rating: str,
                      categories: list[str], was_edited: bool, correction_seconds: int | None,
                      consent: bool, encrypted_example: str | None) -> str:
        feedback_id, p = str(uuid.uuid4()), self.placeholder
        ok = self._execute(f"INSERT INTO feedback VALUES ({','.join([p] * 10)})",
            (feedback_id, workflow_id, session_hash, rating,
             json.dumps(categories, separators=(",", ":")), int(was_edited), correction_seconds,
             int(consent), encrypted_example, utc_now()))
        if not ok:
            raise RuntimeError("feedback_store_unavailable")
        return feedback_id

    def workflow_belongs_to(self, workflow_id: str, session_hash: str) -> bool:
        p = self.placeholder
        rows = self._rows(f"SELECT workflow_id FROM workflows WHERE workflow_id={p} AND session_hash={p}",
                          (workflow_id, session_hash))
        return bool(rows)
    def ready(self) -> dict[str, Any]:
        if not self.enabled:
            return {"configured": False, "reachable": False, "error": "not_configured"}
        reachable = self.ensure_schema()
        return {"configured": True, "reachable": reachable, "error": None if reachable else self.last_error}

    def summary(self, hours: int = 24) -> dict[str, Any]:
        since, p = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(), self.placeholder
        workflows = self._rows(
            f"SELECT workflow_id,session_hash,direction,source_type,input_characters,output_characters,warning_count,"
            f"outcome,error_code,elapsed_ms,model,prompt_version,glossary_sha256,app_version,started_at "
            f"FROM workflows WHERE started_at >= {p}", (since,))
        attempts = self._rows(
            f"SELECT workflow_id,input_tokens,output_tokens,total_tokens,error_code,created_at FROM provider_attempts WHERE created_at >= {p}", (since,))
        feedback = self._rows(
            f"SELECT rating,categories_json,was_edited,consent_to_store_text,created_at FROM feedback WHERE created_at >= {p}", (since,))
        events = self._rows(
            f"SELECT session_hash,event_type,status,error_code,created_at FROM events WHERE created_at >= {p}", (since,))
        outcomes = Counter(row[7] for row in workflows)
        errors = Counter((row[8] or "unknown") for row in workflows if row[7] == "error")
        ratings = Counter(row[0] for row in feedback)
        categories = Counter(item for row in feedback for item in json.loads(row[1] or "[]"))
        active_cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
        active_sessions = {row[0] for row in events if datetime.fromisoformat(row[4]) >= active_cutoff}
        workflow_sessions = {row[0]: row[1] for row in workflows}
        per_session_attempts = Counter(workflow_sessions[row[0]] for row in attempts if row[0] in workflow_sessions)
        hourly = defaultdict(lambda: {"translations": 0, "provider_attempts": 0, "errors": 0})
        karachi = ZoneInfo("Asia/Karachi")
        for row in workflows:
            bucket = datetime.fromisoformat(row[14]).astimezone(karachi).strftime("%Y-%m-%d %H:00")
            hourly[bucket]["translations"] += 1
            hourly[bucket]["errors"] += row[7] == "error"
        for row in attempts:
            hourly[datetime.fromisoformat(row[5]).astimezone(karachi).strftime("%Y-%m-%d %H:00")]["provider_attempts"] += 1
        latencies = sorted(row[9] for row in workflows if isinstance(row[9], int))
        success, terminal = outcomes.get("success", 0), outcomes.get("success", 0) + outcomes.get("error", 0)
        return {
            "window_hours": hours, "translations": len(workflows),
            "success_rate": round(success / terminal * 100, 1) if terminal else None,
            "p95_latency_ms": latencies[min(len(latencies)-1, int(len(latencies)*.95))] if latencies else None,
            "provider_attempts": len(attempts), "total_tokens_reported": sum((row[3] or 0) for row in attempts),
            "translations_with_warnings": sum(1 for row in workflows if (row[6] or 0) > 0),
            "average_warnings": round(sum((row[6] or 0) for row in workflows) / len(workflows), 2) if workflows else 0,
            "anonymous_sessions": len({row[1] for row in workflows}), "active_sessions_15m": len(active_sessions),
            "max_attempts_by_one_session": max(per_session_attempts.values(), default=0),
            "outcomes": dict(outcomes), "errors": dict(errors),
            "directions": dict(Counter(row[2] for row in workflows)),
            "source_types": dict(Counter(row[3] for row in workflows)),
            "feedback": {"ratings": dict(ratings), "categories": dict(categories),
                         "edited": sum(row[2] for row in feedback), "consented_examples": sum(row[3] for row in feedback)},
            "hourly": [{"hour": hour, **values} for hour, values in sorted(hourly.items())],
            "versions": sorted({f"{row[10] or 'unknown'} | {row[11]} | {row[12][:12]} | {row[13][:12]}" for row in workflows}),
        }


telemetry = TelemetryStore()





