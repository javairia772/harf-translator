"""Separate HTTP Basic protection for the developer dashboard."""
import base64
import os
import secrets
from fastapi import HTTPException, Request


def admin_configured() -> bool:
    return bool(os.getenv("ADMIN_USERNAME", "").strip() and len(os.getenv("ADMIN_PASSWORD", "")) >= 20)


def require_admin(request: Request) -> None:
    if not admin_configured():
        raise HTTPException(status_code=404, detail="Developer dashboard is not enabled.")
    expected = b"Basic " + base64.b64encode(
        f"{os.getenv('ADMIN_USERNAME')}:{os.getenv('ADMIN_PASSWORD')}".encode())
    supplied = request.headers.get("authorization", "").encode()
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Developer sign-in required.",
                            headers={"WWW-Authenticate": 'Basic realm="Harf developer"'})
