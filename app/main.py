from pathlib import Path
from time import perf_counter
import asyncio
import json
import os
import secrets
import uuid
from contextlib import suppress

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

# Load local configuration before importing modules that create configured services.
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from .admin import require_admin
from .export import ExportRequest, word_document
from .feedback import FeedbackRequest, encrypt_example
from .observability import anonymous_session, telemetry
from .pilot import PilotGuard
from .translation import (MAX_CHARACTERS, GeminiProvider, ProviderError, TranslationRequest,
                          configuration, fidelity_warnings, glossary_sha256, prompt_version)
from .upload import extract_document, DocumentError, MIME_TYPES

APP_VERSION = os.getenv("RENDER_GIT_COMMIT", os.getenv("APP_VERSION", "local")).strip() or "local"
app = FastAPI(title="English–Urdu Translator", docs_url=None, redoc_url=None)
allowed_hosts = [host.strip() for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]
if os.getenv("APP_ENV") != "production" and "testserver" not in allowed_hosts:
    allowed_hosts.append("testserver")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
app.add_middleware(PilotGuard)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def session_hash(request: Request) -> str:
    return anonymous_session(request.state.session_id)


async def store_call(function, *args, **kwargs):
    return await run_in_threadpool(function, *args, **kwargs)


@app.middleware("http")
async def local_safety(request: Request, call_next):
    raw_session = request.cookies.get("harf_session") or secrets.token_urlsafe(24)
    request.state.session_id = raw_session
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
    response = await call_next(request)
    if "harf_session" not in request.cookies:
        response.set_cookie("harf_session", raw_session, max_age=30 * 24 * 3600,
                            httponly=True, samesite="lax", secure=os.getenv("APP_ENV") == "production")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    return response


@app.exception_handler(RequestValidationError)
async def bad_input(request, exc):
    if request.url.path == "/api/export/docx":
        detail = "Review and approve nonblank text of at most 30,000 characters before downloading."
    elif request.url.path == "/api/feedback":
        detail = "Choose a feedback rating and a problem category when correction is needed."
    else:
        detail = f"Choose a valid translation direction and enter 1–{MAX_CHARACTERS:,} characters of text."
    return JSONResponse({"detail": detail}, status_code=422)


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.get("/readyz")
async def readiness():
    key, model = configuration()
    database = await store_call(telemetry.ready)
    return {"status": "ready" if key and model else "degraded",
            "provider_configured": bool(key and model), "telemetry": database,
            "glossary_available": glossary_sha256() != "missing", "app_version": APP_VERSION[:12]}


@app.post("/api/export/docx")
async def export_docx(body: ExportRequest, request: Request):
    if body.workflow_id:
        await store_call(telemetry.record_event, session_hash=session_hash(request),
                         event_type="reviewed_download", workflow_id=str(body.workflow_id),
                         metadata={"format": "docx", "characters": len(body.text)})
    content = await run_in_threadpool(word_document, body.text, body.direction)
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="reviewed-translation.docx"'})


upload_slots = asyncio.Semaphore(2)


@app.post("/api/upload")
async def upload_document(request: Request):
    started = perf_counter()
    kind = MIME_TYPES.get(request.headers.get("content-type", "").split(";")[0].strip().lower())
    if kind is None:
        await store_call(telemetry.record_event, session_hash=session_hash(request), event_type="upload",
                         status="error", error_code="unsupported_type")
        return JSONResponse({"detail": "Choose a Word (.docx) or text-based PDF (.pdf) file."}, status_code=415)
    if upload_slots.locked():
        return JSONResponse({"detail": "Two documents are already being read. Please try again shortly."}, status_code=429)
    try:
        async with upload_slots:
            result = await run_in_threadpool(extract_document, await request.body(), kind)
        await store_call(telemetry.record_event, session_hash=session_hash(request), event_type="upload",
                         duration_ms=round((perf_counter()-started)*1000),
                         metadata={"source_type": kind, "characters": result["characters"],
                                   "pages": len(result.get("pages", [])), "warnings": len(result.get("warnings", []))})
        return result
    except DocumentError as error:
        await store_call(telemetry.record_event, session_hash=session_hash(request), event_type="upload",
                         status="error", error_code="extraction_failed",
                         duration_ms=round((perf_counter()-started)*1000), metadata={"source_type": kind})
        return JSONResponse({"detail": str(error)}, status_code=422)


@app.get("/")
async def home():
    return FileResponse(ROOT / "static/index.html")


@app.get("/api/status")
async def status(request: Request):
    key, model = configuration()
    await store_call(telemetry.record_event, session_hash=session_hash(request), event_type="session_active")
    return {"configured": bool(key and model), "max_characters": MAX_CHARACTERS,
            "feedback_enabled": telemetry.enabled}


def get_provider():
    return GeminiProvider()


def workflow_details(body: TranslationRequest, request: Request):
    return str(uuid.uuid4()), str(uuid.uuid4()), session_hash(request)


async def begin_workflow(body: TranslationRequest, request: Request):
    workflow_id, request_id, hashed = workflow_details(body, request)
    await store_call(telemetry.start_workflow, workflow_id=workflow_id, request_id=request_id,
                     session_hash=hashed, direction=body.direction, source_type=body.source_type,
                     input_characters=len(body.text), prompt_version=prompt_version(),
                     glossary_sha256=glossary_sha256(), app_version=APP_VERSION)
    await store_call(telemetry.record_event, session_hash=hashed, event_type="translation_started",
                     workflow_id=workflow_id, request_id=request_id,
                     metadata={"source_reviewed": body.source_reviewed})
    return workflow_id, request_id, hashed


async def finish_success(workflow_id: str, request_id: str, hashed: str, provider,
                         result, warnings: list[str], elapsed_ms: int):
    metadata = getattr(provider, "metadata", {})
    await store_call(telemetry.finish_workflow, workflow_id, outcome="success", elapsed_ms=elapsed_ms,
                     output_characters=len(result.translation), warning_count=len(warnings),
                     model=metadata.get("model"), prompt_sha256=metadata.get("prompt_sha256"))
    await store_call(telemetry.record_provider_summary, workflow_id, metadata)
    await store_call(telemetry.record_event, session_hash=hashed, event_type="translation_finished",
                     workflow_id=workflow_id, request_id=request_id, duration_ms=elapsed_ms,
                     metadata={"warnings": len(warnings), "notes": len(result.notes)})


async def finish_error(workflow_id: str, request_id: str, hashed: str, provider,
                       error_code: str, elapsed_ms: int):
    metadata = getattr(provider, "metadata", {})
    await store_call(telemetry.finish_workflow, workflow_id, outcome="error", elapsed_ms=elapsed_ms,
                     error_code=error_code, model=metadata.get("model"),
                     prompt_sha256=metadata.get("prompt_sha256"))
    await store_call(telemetry.record_provider_summary, workflow_id, metadata)
    await store_call(telemetry.record_event, session_hash=hashed, event_type="translation_finished",
                     status="error", error_code=error_code, workflow_id=workflow_id,
                     request_id=request_id, duration_ms=elapsed_ms)


@app.post("/api/translate")
async def translate(body: TranslationRequest, request: Request, provider=Depends(get_provider)):
    start = perf_counter()
    workflow_id, request_id, hashed = await begin_workflow(body, request)
    try:
        result = await provider.translate(body)
    except ProviderError as error:
        await finish_error(workflow_id, request_id, hashed, provider, error.code,
                           round((perf_counter()-start)*1000))
        return JSONResponse({"detail": error.message, "workflow_id": workflow_id}, status_code=error.status)
    warnings = fidelity_warnings(body.text, result.translation)
    elapsed = round((perf_counter()-start)*1000)
    await finish_success(workflow_id, request_id, hashed, provider, result, warnings, elapsed)
    return {**result.model_dump(), "warnings": warnings, "elapsed_ms": elapsed, "workflow_id": workflow_id}


@app.post("/api/translate/stream")
async def translate_stream(body: TranslationRequest, request: Request, provider=Depends(get_provider)):
    workflow_id, request_id, hashed = await begin_workflow(body, request)

    async def events():
        queue, start = asyncio.Queue(), perf_counter()

        async def work():
            try:
                result = await provider.translate(body, progress=queue.put_nowait)
                warnings = fidelity_warnings(body.text, result.translation)
                elapsed = round((perf_counter()-start)*1000)
                await finish_success(workflow_id, request_id, hashed, provider, result, warnings, elapsed)
                queue.put_nowait({"type": "result", **result.model_dump(), "warnings": warnings,
                                  "elapsed_ms": elapsed, "workflow_id": workflow_id})
            except ProviderError as error:
                elapsed = round((perf_counter()-start)*1000)
                await finish_error(workflow_id, request_id, hashed, provider, error.code, elapsed)
                queue.put_nowait({"type": "error", "detail": error.message, "code": error.code,
                                  "status": error.status, "workflow_id": workflow_id})
            except Exception:
                elapsed = round((perf_counter()-start)*1000)
                await finish_error(workflow_id, request_id, hashed, provider, "internal_error", elapsed)
                queue.put_nowait({"type": "error", "detail": "Translation could not be completed. Your text is still here; please try again.",
                                  "status": 500, "workflow_id": workflow_id})

        task = asyncio.create_task(work())
        try:
            while True:
                item = await queue.get()
                yield json.dumps(item, ensure_ascii=False) + "\n"
                if item["type"] in ("result", "error"):
                    break
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    return StreamingResponse(events(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})


@app.post("/api/feedback")
async def submit_feedback(body: FeedbackRequest, request: Request):
    if not telemetry.enabled:
        return JSONResponse({"detail": "Feedback storage is not configured yet."}, status_code=503)
    hashed = session_hash(request)
    if not await store_call(telemetry.workflow_belongs_to, str(body.workflow_id), hashed):
        return JSONResponse({"detail": "This translation session could not be verified."}, status_code=404)
    try:
        encrypted = encrypt_example(body)
        feedback_id = await store_call(telemetry.save_feedback, workflow_id=str(body.workflow_id),
            session_hash=hashed, rating=body.rating, categories=body.categories,
            was_edited=body.was_edited, correction_seconds=body.correction_seconds,
            consent=body.consent_to_store_text, encrypted_example=encrypted)
    except RuntimeError as error:
        detail = "Encrypted example storage is not configured." if "encryption" in str(error) else "Feedback could not be saved."
        return JSONResponse({"detail": detail}, status_code=503)
    await store_call(telemetry.record_event, session_hash=hashed, event_type="feedback_submitted",
                     workflow_id=str(body.workflow_id), metadata={"rating": body.rating, "consented": body.consent_to_store_text})
    return {"saved": True, "feedback_id": feedback_id}


@app.get("/admin")
async def admin_page(_: None = Depends(require_admin)):
    return FileResponse(ROOT / "static/admin.html")


@app.get("/api/admin/summary")
async def admin_summary(hours: int = 24, _: None = Depends(require_admin)):
    if hours not in (1, 6, 24, 72, 168, 720):
        return JSONResponse({"detail": "Choose a supported reporting window."}, status_code=422)
    if not telemetry.enabled:
        return JSONResponse({"detail": "Set DATABASE_URL to enable telemetry."}, status_code=503)
    return await store_call(telemetry.summary, hours)
