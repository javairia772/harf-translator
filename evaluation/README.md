# Reusable translation evaluation

Add cases to JSONL data files; do not create a new Python script per document.

```powershell
# Validate data and show the request budget without calling a model:
.\.venv\Scripts\python.exe evaluation/run.py --dataset evaluation/datasets/real-cases.jsonl

# Compare basic prompting against contextual prompting on the same model:
.\.venv\Scripts\python.exe evaluation/run.py --dataset evaluation/datasets/real-cases.jsonl --live --output evaluation-results/my-new-run

# Compare explicit models without changing the browser app's model:
.\.venv\Scripts\python.exe evaluation/run.py --dataset evaluation/datasets/real-cases.jsonl --models MODEL_A MODEL_B --live
```

Default limit: 4 cases. Default profiles: basic and domain. Each evaluation allows at most three attempts in 90 seconds; failed requests may still incur provider usage. Text is sent only with --live. The runner saves evaluation source/output locally because this is an explicit evaluation, unlike the ordinary browser translator. Only use consented, de-identified or public texts.

Use --docx-workflow to additionally verify exact source extraction from an in-memory Word document and exact translated-text readback from Word export. This does not simulate human approval or verify visual layout. datasets/upload-cases.jsonl contains six examples (three per direction), including two explicitly synthetic Urdu examples. Use --profiles domain --limit 6 to run one variant on all six. The upload endpoint and approval controls are covered separately by automated tests with fixture translations.

Optional dedicated translation baseline: configure GOOGLE_TRANSLATE_API_KEY for a project with Cloud Translation enabled, then add --include-google. The runner will not enable an API, alter billing, reuse the Gemini key implicitly, or guess a model. Without that setup, same-model basic prompting is an **ablation baseline**, not evidence of superiority to Google Translate.

Each output directory must be new. No automatic reuse across prompts/models or replacement of previous evidence. Results include input provenance, direction, model, prompt-content hash, usage when returned, attempt count, failures, latency and diagnostic warnings. Cost is unknown until the applicable provider pricing is supplied. Review templates initialize scores to null, never zero or automatic passing scores.

## Interpretation

- NOC-REGRESSION is known development material and cannot substantiate generalization.
- Public snippets in real-cases.jsonl are exploratory tests, not a statistically representative benchmark. Only one is original Urdu.
- Synthetic seeds in docs/translation-development-cases.json remain synthetic even if they sound realistic.
- Keep independent held-out documents and their close paraphrases out of prompt/glossary tuning. Split by original document family.
- Have two bilingual reviewers assess randomized, anonymized outputs against meaning, terminology, fluency and correction effort. Record critical errors separately. Consult domain reviewers when interpreting clauses requires expertise.
- A lack of automated warnings does not mean the translation is faithful. Round trips cannot replace original Urdu-to-English tests.

Use run.py for all new evaluations. The superseded one-off NOC script was removed; its historical results remain in evaluation-results.
