"""Bounded retries. No document contents or provider secrets enter progress events."""
import asyncio
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import monotonic


class ProviderError(Exception):
    def __init__(self, message, status=502, *, retryable=False, retry_after=None, code="provider_error"):
        self.message, self.status = message, status
        self.retryable, self.retry_after, self.code = retryable, retry_after, code


def retry_delay(response):
    value = response.headers.get("retry-after")
    if value:
        try:
            return max(0, float(value))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    try:
        for detail in response.json().get("error", {}).get("details", []):
            if "retryDelay" in detail:
                return max(0, float(str(detail["retryDelay"]).removesuffix("s")))
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def exhausted_quota(response):
    # Inspect internally, never show raw provider messages or quota metadata.
    try:
        error = response.json().get("error", {})
        message = str(error.get("message", "")).lower()
        ids = [str(v.get("quotaId", "")).lower()
               for d in error.get("details", []) if isinstance(d, dict)
               for v in d.get("violations", []) if isinstance(v, dict)]
        return any("perday" in q or "daily" in q for q in ids) or any(
            word in message for word in ("daily quota", "daily limit", "billing is disabled", "billing account is disabled", "spend limit", "spending limit"))
    except (ValueError, TypeError, AttributeError):
        return False


async def with_retries(operation, *, progress=None, metadata=None, max_attempts=3,
                       deadline=90, sleep=asyncio.sleep, clock=monotonic, jitter=random.random):
    start = clock()
    metadata = metadata if metadata is not None else {}
    metadata.update(attempts=0, retries=0, error_codes=[])
    for attempt in range(1, max_attempts + 1):
        remaining = deadline - (clock() - start)
        if remaining <= 0:
            break
        metadata.update(attempts=attempt, retries=attempt - 1)
        if progress:
            progress({"type":"progress", "state":"translating", "attempt":attempt, "max_attempts":max_attempts})
        try:
            return await asyncio.wait_for(operation(), timeout=remaining)
        except asyncio.TimeoutError:
            raise ProviderError("Translation reached its time limit. Your text is still here; please try again.", 504, code="deadline") from None
        except ProviderError as error:
            metadata["error_codes"].append(error.code)
            if not error.retryable or attempt == max_attempts:
                raise
            delay = error.retry_after if error.retry_after is not None else 2 ** attempt + jitter()
            if delay >= deadline - (clock() - start):
                raise ProviderError("The provider needs a longer wait. Your text is still here; please try again later.", error.status, code="retry_wait_exceeds_deadline") from None
            if progress:
                progress({"type":"progress", "state":"retrying", "attempt":attempt + 1,
                          "max_attempts":max_attempts, "wait_seconds":round(delay, 1)})
            await sleep(delay)
    raise ProviderError("Translation reached its time limit. Please try again.", 504, code="deadline")
