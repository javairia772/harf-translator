# Translation quality and reliability: implementation decisions

The implemented scope remains pasted English/Urdu text for office and stamp-paper language.

| What we use | Why | Alternative and tradeoff |
|---|---|---|
| Contextual prompt and a draft glossary | Address observed employment, CNIC and rules/regulations errors | Fine-tuning needs substantially more reviewed data; no evidence justifies it yet |
| Exact placeholder validation | Prevent changed bracketed fields, including repeated fields and case | Prompt-only preservation was insufficient; a rejected result can require another paid attempt |
| Number, acronym and clause warnings | Make detectable omissions visible | These are not a substitute for bilingual semantic review |
| At most three attempts within 90 seconds | Recover from transient provider failures without indefinite waiting | Immediate failure is faster but less resilient; retries add latency and usage |
| Streamed status events | Show actual attempt/retry state | A spinner alone conceals progress; this does not stream partial translated prose |
| One JSONL dataset and runner | Test new documents without new scripts | One-off scripts make comparisons hard to reproduce |
| Basic/domain prompt comparison with blinded review packets | Separate prompt effects and enable independent review | Model self-scoring does not establish accuracy |

Run instructions: evaluation/README.md. Automated tests are offline and make no provider calls. Live runs are explicit, retain failures, and record attempts, latency and available usage metadata. Evaluation artifacts contain source/output text; unlike the normal app, the evaluation workflow deliberately saves these locally. Use permitted, de-identified inputs.

The current provider is a candidate, not a proven winner. Google Translation comparison requires its own enabled credentials. No API or billing was enabled automatically. No fallback provider silently receives documents.

## Stage gates

1. Reliability and reusable evaluation are implemented. The small live run exposed provider availability limits; final newline validation and quota classification refinements passed offline tests but have not been rechecked live.
2. Continue quality evaluation with successful original Urdu inputs, a dedicated translation baseline, and independent bilingual scoring. Keep the known NOC regression separate from held-out data. Current results are insufficient for a release-quality claim.
3. After the text workflow passes review, add explicit user approval and DOCX/PDF export from the exact reviewed revision. Editing afterward must invalidate approval. Test Urdu shaping, right-to-left paragraphs, mixed identifiers, pagination and fonts.
4. Add DOCX and text-based PDF upload with extracted-text preview and correction before translation. Extraction errors must be distinguishable from translation errors.
5. Add scanned PDF/image OCR only after extraction accuracy can be evaluated independently. Let users correct OCR before translating.

Update: user approved the private-pilot workflow with approval and Word download, then Word and text-based PDF inputs. Those features are implemented through one upload endpoint and the same translator. PDF export and OCR remain future scope. Word visual validation and deployment smoke testing remain release gates. See private-pilot.md and pdf-upload-testing.md.
