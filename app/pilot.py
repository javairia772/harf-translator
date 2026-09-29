"""Single-process private-pilot access, body and translation budget limits."""
import base64
import os
import secrets
from collections import deque
from time import monotonic

from starlette.responses import JSONResponse


class PilotGuard:
    def __init__(self, app):
        self.app = app
        self.production = os.getenv("APP_ENV") == "production"
        access = os.getenv("ACCESS_MODE", "private").strip().lower()
        if access not in ("private", "public"):
            raise RuntimeError("ACCESS_MODE must be private or public.")
        self.public = access == "public"
        self.username = os.getenv("PILOT_USERNAME", "")
        self.password = os.getenv("PILOT_PASSWORD", "")
        if self.production and (not os.getenv("ALLOWED_HOSTS", "").strip() or "*" in os.getenv("ALLOWED_HOSTS", "")):
            raise RuntimeError("Production requires explicit ALLOWED_HOSTS without wildcards.")
        if self.production and not self.public and (not self.username or len(self.password) < 20):
            raise RuntimeError("Private mode requires PILOT_USERNAME and PILOT_PASSWORD (at least 20 characters).")
        self.calls = deque()
        self.active = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        async def reject(status, detail, headers=None):
            await JSONResponse({"detail": detail}, status_code=status, headers={"Cache-Control":"no-store", **(headers or {})})(scope, receive, send)
        headers = dict(scope["headers"])
        public_system_path = scope["path"] in ("/healthz", "/readyz", "/static/admin.js", "/static/admin.css") or scope["path"].startswith("/admin") or scope["path"].startswith("/api/admin/")
        if not public_system_path and not self.public and (self.production or self.password):
            expected = b"Basic " + base64.b64encode((self.username + ":" + self.password).encode())
            if not secrets.compare_digest(headers.get(b"authorization", b""), expected):
                return await reject(401, "Private pilot sign-in required.", {"WWW-Authenticate": 'Basic realm="Harf pilot"'})
        chunks, total = [], 0
        if scope["method"] == "POST":
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    return
                part = event.get("body", b"")
                total += len(part)
                limit = 2 * 1024 * 1024 if scope['path'] == '/api/upload' else 262144
                if total > limit:
                    return await reject(413, "Request is too large.")
                chunks.append(part)
                if not event.get("more_body", False):
                    break
        replayed = False
        async def replay():
            nonlocal replayed
            if not replayed and scope["method"] == "POST":
                replayed = True
                return {"type":"http.request", "body":b"".join(chunks), "more_body":False}
            return await receive()
        limited = (self.production or bool(self.password)) and scope["method"] == "POST" and scope["path"] in ("/api/translate", "/api/translate/stream")
        if limited:
            now = monotonic()
            while self.calls and now - self.calls[0] >= 3600:
                self.calls.popleft()
            if self.active >= 2 or len(self.calls) >= 30:
                return await reject(429, "Pilot translation limit reached. Please try again later.", {"Retry-After":"60"})
            self.calls.append(now)
            self.active += 1
        try:
            await self.app(scope, replay, send)
        finally:
            if limited:
                self.active -= 1
