# AI engineering operations

## Why this layer exists

A successful HTTP response does not prove that a translation system is useful. The pilot must show where a workflow failed, how often the model was retried, whether users corrected the result, and when shared API demand is highest. It must collect that evidence without silently saving office or stamp-paper text.

## What one workflow records

| Step | Stored evidence | Why |
|---|---|---|
| Page activity | Hashed anonymous browser session and time | Estimate recent use without user accounts |
| Upload | File type, extracted character/page counts, warning count, outcome | Separate extraction failures from model failures |
| Translation start | Direction, source type, character count, source-review confirmation, request/workflow IDs | Reconstruct the stage reached without the source text |
| Provider completion | Attempt count, safe error code, latency, model, reported token counts | Find quota, reliability, cost and performance problems |
| Model result | Output length, review-warning count, prompt/glossary/app versions | Compare releases using reproducible versions |
| Human feedback | Rating, problem categories, whether edited, correction time | Measure usefulness with user review rather than an invented confidence score |
| Download | Format and reviewed character count | Confirm that a user completed the review workflow |

The ordinary telemetry tables never store source text, model output, filenames, IP addresses or API keys. Browser sessions are random cookies and become one-way hashes before storage. “Active users” means unique hashed sessions with activity in the last 15 minutes; it is an operational estimate, not a count of people.

## Human feedback

The user can mark a result as good, corrected or unusable. Corrected and unusable results require a category such as meaning, terminology, name, number/date, formatting or fluency. This supports acceptance rate, correction rate and error-category trends.

A corrected source/result pair is included only when the user checks the separate consent box. The three texts are encrypted together with Fernet before entering the database. `FEEDBACK_ENCRYPTION_KEY` stays in Render's secret environment. The dashboard shows only aggregate counts and never decrypts examples. Export consented examples later with a separate authorized offline evaluation script; do not expose decryption in the web application.

## Review indicators instead of confidence

The system does not display a confidence percentage because the provider does not supply a calibrated probability of translation correctness. It exposes evidence that a reviewer can act on: changed numbers, missing acronyms, changed placeholders, changed clause count, model ambiguity notes, user edits and feedback categories. Accuracy is established with a versioned bilingual evaluation set and reviewer scoring, not with a self-reported model score.

## Developer dashboard

`/admin` has separate HTTP Basic credentials from the pilot. It shows translations, terminal success rate, provider attempts and reported tokens, review warnings, p95 latency, anonymous/recent sessions, the largest per-session API use, errors, feedback and traffic by Pakistan hour. Set `ADMIN_USERNAME` and an `ADMIN_PASSWORD` of at least 20 characters. If they are absent, the route is disabled.

`/healthz` proves that the web process can answer Render. `/readyz` reports provider configuration, glossary availability, application version and database reachability without calling Gemini. Translation continues if telemetry temporarily fails; readiness exposes the failure so it can be repaired.

## Render setup

1. Create a Render PostgreSQL database in the same region as the web service.
2. Copy its internal database URL into the web service variable `DATABASE_URL`.
3. Add long random values for `SESSION_HASH_SALT`, `ADMIN_PASSWORD` and the existing pilot credentials.
4. Generate `FEEDBACK_ENCRYPTION_KEY` locally with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and save only the output in Render.
5. Set `PROMPT_VERSION=translation-v1`. Change this value whenever prompt behavior changes.
6. Keep Render's health-check path at `/healthz`, deploy, then verify `/readyz` and sign in to `/admin` with the developer credentials.

The prompt and glossary are included on each stateless Gemini request because each request must be understandable on its own. A long-lived prompt cache could reduce repeated input tokens later, but it adds provider-specific expiry and invalidation behavior. Measure input-token cost first; add caching only if the measured saving justifies that complexity.
