# Private pilot release

Access update: ACCESS_MODE defaults to private. Set ACCESS_MODE=public explicitly to remove the browser sign-in prompt. Public mode permits anyone to consume the shared provider quota; production request/body limits and explicit ALLOWED_HOSTS remain enforced. Keep APP_ENV=production. Pilot credentials are ignored in public mode. Rate limits reset on restart and are not a spending cap.

Scope: pasted text or simple .docx/text-based PDF upload, extracted-text preview and confirmation, editable translation, explicit approval and Word download. No image/legacy .doc uploads, OCR, PDF export, user accounts or saved document history. Operational workflow events are stored without document text when DATABASE_URL is configured.

Word uploads are limited to 2 MiB compressed, 8 MiB expanded and 256 ZIP members; extracted text must fit the existing 5,000-character limit. Parsing is in memory with network/entity resolution disabled. Paragraphs and simple tables are supported; table layout is flattened and requires review. Images, fields, automatic numbering, revisions, headers/footers with text, notes and complex structures are rejected to avoid silent omissions. The uploaded file is not sent to the translation provider or saved by the app.

Both formats use /api/upload. PDF limits are 2 MiB, 1–10 pages and 5,000 extracted characters. Image-bearing pages (including logos), unreadable/blank pages, encryption, form fields and non-link annotations are conservatively rejected with no partial import. Some Urdu fonts still produce missing or reordered words despite usable Unicode; preview and manual confirmation are mandatory in the UI. This is not guaranteed automatic extraction fidelity.

Extraction runs in a short-lived subprocess with a 12-second deadline, two concurrent uploads at most, and no provider credentials passed to the child. Linux workers have a 256 MiB address-space limit; Windows has byte, stream, page and time limits but no hard process-memory cap. No file content is written to disk by the application.

The browser binds approval to the current source, direction and translated text. Editing any of these, clearing, or starting translation revokes approval. Export sends the exact reviewed text to a deterministic python-docx function, without a model call. The server requires an affirmative approval field; this is a user acknowledgement, not a tamper-proof audit trail or legal certification. Plain text and line breaks are preserved; Markdown markers remain literal. This release does not reconstruct original forms or stamp-paper layouts.

Word uses A4 pages, Arial 12pt, generous line spacing, right-to-left paragraphs for Urdu and explicit run direction for mixed Latin identifiers. Fonts are not embedded. Rendering can differ between Word installations; a real Word visual review is required before releasing to users.

## Deployment preparation

The Dockerfile packages only the runtime application and static assets. .dockerignore excludes keys, evaluation documents, notes and temporary files. Deploy one container with one worker. The included shared pilot access uses browser HTTP Basic authentication and must only be used behind HTTPS. Use the hosting secret store, never Docker build arguments or committed .env files.

Required production variables:

```
APP_ENV=production
ALLOWED_HOSTS=your-exact-hostname.example
PILOT_USERNAME=your-pilot-user
PILOT_PASSWORD=<random secret of at least 20 characters>
GEMINI_API_KEY=<rotated provider key>
GEMINI_MODEL=<available tested model>
ACCESS_MODE=private
DATABASE_URL=<Render PostgreSQL internal URL>
SESSION_HASH_SALT=<long random value>
ADMIN_USERNAME=<separate developer user>
ADMIN_PASSWORD=<random secret of at least 20 characters>
PROMPT_VERSION=translation-v1
FEEDBACK_ENCRYPTION_KEY=<Fernet key for explicitly consented examples>
```

Set the health-check path to /healthz. Production startup fails without explicit hosts and pilot credentials. Behind a proxy, ensure Uvicorn trusts forwarded headers only from that proxy so same-origin requests see the correct HTTPS scheme; configure FORWARDED_ALLOW_IPS for the actual hosting environment. Never add wildcard allowed hosts to work around a configuration problem.

The pilot allows at most two concurrent translation requests and 30 requests per rolling hour across all pilot users. Each request can make up to three provider attempts. Limits are in-memory and reset on restart; they are not a billing cap and must not be used across multiple workers/replicas. Configure provider-side quotas/budget alerts as well. POST bodies are limited to 256 KiB. Health checks do not expose provider credentials or prove provider availability.

## Release checks

- Rotate the key previously placed in .env.example; that file now contains placeholders only.
- Select the hosting account and review its costs before publishing.
- Run backend and frontend tests, build and smoke-test the container, then check HTTPS authentication, allowed hosts, streaming and Word download on the target host.
- Visually inspect exported original Urdu and English cases in Word, including mixed CNIC identifiers and multiple pages.
- Complete successful Urdu-to-English model evaluation and independent bilingual review. Existing exploratory results are not a general accuracy benchmark.

No public deployment has been performed. Container execution and Word visual validation must be reported separately from automated structural tests.
