# Harf — English ↔ Urdu text translator

A private-pilot prototype for translating pasted text or text extracted from Word (.docx) and text-based PDF documents, reviewing/editing it, approving the current revision and downloading that text as Word. No document drafting, accounts database, saved history, OCR or agents.

## Run on Windows / PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
# Edit .env locally: set GEMINI_API_KEY and GEMINI_MODEL.
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Without credentials the interface works, but translation is visibly disconnected. No canned translations or fallback demo outputs are presented as live results.

Runtime dependency versions are pinned in requirements-lock.txt for the Docker image. Test-only packages are installed separately through requirements-dev.txt. The broader requirements files describe compatible ranges. One upstream Starlette warning currently reports a forthcoming test-client HTTPX migration; it does not fail the tests.

Choose a currently available model ID from your account that supports generateContent structured output. The implementation uses Google's documented REST endpoint, x-goog-api-key header, systemInstruction and responseSchema. The provider is an initial integration candidate, not a benchmark winner. Model ID is explicit configuration to avoid silently changing versions. Provider usage may incur charges: review account pricing, data handling and budget limits before live use. Do not share keys in chat or commit .env.

## What we use and why

| Choice | Reason | Alternative considered |
|---|---|---|
| Python + FastAPI | Input validation and backend also suitable for later evaluation tooling | Separate languages for evaluation and server |
| Plain HTML/CSS/JavaScript | Two text panes need no frontend build system | React and a larger dependency tree |
| HTTPX calling Gemini REST | A small, isolated provider with timeout/error handling and mockable transport | Agent framework or provider SDK for this one endpoint |
| Prompt + draft glossary | Inspectable, inexpensive adaptation | Training/fine-tuning before gathering evidence |
| Pydantic response validation | Reject malformed, blank and incomplete responses | Blindly displaying provider text |
| Number/acronym checks | Flag simple changes for review | Claiming automatic semantic verification |
| No persistence | Current task does not require history | MongoDB and account management |
| python-docx export | Export reviewed wording without another model call | AI rewriting or reconstructing form layouts |

## Core functional requirements

- FR1: Accept 1–5,000 characters of nonblank pasted text and an explicit en-ur or ur-en direction; reject excess input without truncation.
- FR2: Return a real provider translation with notes separated from the translated text.
- FR3: Apply faithful-translation instructions and contextual terminology without drafting additional clauses.
- FR4: Support Urdu right-to-left input/output and editable text; copy the current result.
- FR5: Preserve source text on provider errors; display actionable configuration, quota and timeout errors.
- FR6: Flag changed numeric tokens or missing uppercase abbreviations; distinguish warnings from proof of correctness.
- FR7: Mark output stale and disable copying after source changes; clear the result when direction changes. Confirm before overwriting an existing edited result.
- FR8: Keep keys server-side and do not persist source/output in this app. Third-party provider policies still apply.
- FR9: Require approval of the current revision before Word download; edits, source/direction changes and retranslations revoke approval.
- FR10: Export reviewed text and line breaks with Urdu paragraph direction and mixed-script runs. No added clauses or certification.
- FR11: One attachment button under the source accepts .docx and text-based .pdf files (2 MiB, 5,000 extracted characters, at most 10 PDF pages). Review/correct the extracted text and confirm before translating. PDF page previews preserve page boundaries; simple Word tables become tab-separated text. Images/scans and unsupported structures receive explicit errors. Extraction stays on the app server; text goes to the provider only when Translate is pressed. Files are not saved. See docs/pdf-upload-testing.md for coverage and known Urdu limitations.

Deployment setup and remaining release gates are documented in docs/private-pilot.md. Production mode requires pilot credentials and explicit allowed hosts. The Dockerfile is prepared but has not been executed in this environment. DOCX structural tests pass; visual rendering remains blocked by the missing renderer.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check static/app.js
node --test tests/frontend.test.cjs
```

Tests use injected providers and HTTP mocks: no API credentials, charges or external text transmission. They check request validation, both numerical scripts, changed amounts/acronyms, provider failures, incomplete generation and prompt separation. Passing these tests does **not** establish translation quality or prove prompt-injection resistance.

Use the reusable runner documented in evaluation/README.md; add cases to its JSONL dataset instead of creating a new Python script per document. The first live comparison completed 5 of 8 runs on four document excerpts/inputs, with three provider failures. See evaluation-results/stage-ab-live/review.md. This exploratory comparison does not establish general accuracy. The glossary and seed references still require bilingual review.

## Limits before wider deployment

This is a localhost prototype, bound to 127.0.0.1 with trusted-host and cross-origin checks. Do not expose it publicly without authentication, request size/rate limits, TLS and a reviewed provider budget. Transient failures and invalid model outputs can retry automatically, limited to three attempts and a 90-second total deadline; retries can incur additional usage. Configuration errors and identified exhausted daily quotas do not retry. The interface displays progress and retains input on failure. Placeholder changes reject the output; number, acronym and clause checks flag possible problems. These checks cannot establish semantic correctness. Human review remains necessary. Nothing here certifies a document's legal validity.

Provider docs: https://ai.google.dev/api and https://ai.google.dev/gemini-api/docs/structured-output
