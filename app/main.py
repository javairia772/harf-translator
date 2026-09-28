from pathlib import Path
from time import perf_counter
import asyncio
import json
import os
from contextlib import suppress

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .translation import MAX_CHARACTERS, GeminiProvider, ProviderError, TranslationRequest, configuration, fidelity_warnings
from .export import ExportRequest, word_document
from .pilot import PilotGuard
from .upload import extract_document, DocumentError, MIME_TYPES
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
app = FastAPI(title="English–Urdu Translator", docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(","))
app.add_middleware(PilotGuard)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def local_safety(request: Request, call_next):
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    return response


@app.exception_handler(RequestValidationError)
async def bad_input(request, exc):
    if request.url.path == "/api/export/docx":
        return JSONResponse({"detail": "Review and approve nonblank text of at most 30,000 characters before downloading."}, status_code=422)
    return JSONResponse({"detail": f"Choose a valid translation direction and enter 1–{MAX_CHARACTERS:,} characters of text."}, status_code=422)


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.post("/api/export/docx")
def export_docx(body: ExportRequest):
    return Response(word_document(body.text, body.direction),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="reviewed-translation.docx"'})


upload_slots = asyncio.Semaphore(2)


@app.post("/api/upload")
async def upload_document(request: Request):
    kind = MIME_TYPES.get(request.headers.get('content-type', '').split(';')[0].strip().lower())
    if kind is None:
        return JSONResponse({'detail':'Choose a Word (.docx) or text-based PDF (.pdf) file.'}, status_code=415)
    if upload_slots.locked():
        return JSONResponse({'detail':'Two documents are already being read. Please try again shortly.'}, status_code=429)
    try:
        async with upload_slots:
            return await run_in_threadpool(extract_document, await request.body(), kind)
    except DocumentError as error:
        return JSONResponse({'detail':str(error)}, status_code=422)


@app.get("/")
async def home():
    return FileResponse(ROOT / "static/index.html")


@app.get("/api/status")
async def status():
    key, model = configuration()
    return {"configured": bool(key and model), "max_characters": MAX_CHARACTERS}


def get_provider():
    return GeminiProvider()


@app.post("/api/translate")
async def translate(body: TranslationRequest, provider=Depends(get_provider)):
    start = perf_counter()
    try:
        result = await provider.translate(body)
    except ProviderError as error:
        return JSONResponse({"detail": error.message}, status_code=error.status)
    return {**result.model_dump(), "warnings": fidelity_warnings(body.text, result.translation),
            "elapsed_ms": round((perf_counter() - start) * 1000), "metadata":getattr(provider, "metadata", {})}


@app.post("/api/translate/stream")
async def translate_stream(body: TranslationRequest, provider=Depends(get_provider)):
    async def events():
        queue = asyncio.Queue()
        start = perf_counter()

        async def work():
            try:
                result = await provider.translate(body, progress=queue.put_nowait)
                queue.put_nowait({"type":"result", **result.model_dump(),
                    "warnings":fidelity_warnings(body.text, result.translation),
                    "elapsed_ms":round((perf_counter() - start) * 1000),
                    "metadata":getattr(provider, "metadata", {})})
            except ProviderError as error:
                queue.put_nowait({"type":"error", "detail":error.message, "code":error.code, "status":error.status})
            except Exception:
                # Do not send stack traces, request text or provider response bodies.
                queue.put_nowait({"type":"error", "detail":"Translation could not be completed. Your text is still here; please try again.", "status":500})

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
    return StreamingResponse(events(), media_type="application/x-ndjson", headers={"X-Accel-Buffering":"no"})
