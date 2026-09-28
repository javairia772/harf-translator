# Word and PDF attachment workflow

One paperclip below the source pane opens a picker for .docx and .pdf. The server checks file content and extracts locally. PDF pages have individual read-only previews; the combined source uses blank lines between pages, without adding page labels to the translation. The user edits the source and confirms that it matches the original before translating. Source edits reset this confirmation. Translation review/approval is separate and is cleared by source/output changes. Removing an attachment confirms before clearing its text and translation. Failed/cancelled uploads retain existing work.

## Implementation choices

Use pinned pypdf 6.19.0 for PDF text extraction, retaining the existing Word extractor and Word exporter. This avoids OCR for digitally generated text PDFs. No new model, retrieval system, database or upload service is involved. The obsolete Word-only route and separate upload section were removed; a common validation helper handles extracted-text limits and unreadable characters.

The parser runs in a terminable subprocess so a timeout stops the work, rather than leaving a thread processing a malformed file. File/page/stream/decompression limits and limited concurrency bound ordinary use; they are not a claim that arbitrary hostile PDFs are safe. Linux adds a memory limit. Deployment container testing remains outstanding.

## Test coverage

- English and Unicode Urdu PDF extraction through upload, fixture translation, approval acknowledgement and exact Word readback, two cases per direction.
- Paragraphs, simple Word tables, page boundaries, mixed identifiers, negation, amounts and deadlines.
- Corrupt/non-PDF bytes, wrong declared type, empty files/pages, encrypted PDFs, more than 10 pages, oversized uploads/text/streams, image-only scans, mixed text/scan pages, image-bearing text pages and annotations/form widgets.
- Word ZIP expansion, revisions, headers, empty documents and oversized text remain covered.
- Worker timeout message; the subprocess implementation terminates its child when the deadline expires.
- UI file replacement, failed uploads, invalid extensions/sizes, cancelled replacement, PDF previews, removal, extraction review confirmation and approval invalidation.

These are representative best/boundary/failure cases, not every possible file. Synthetic Unicode parser fixtures are not evidence of visually correct font rendering. Model responses in workflow tests are controlled fixtures, not quality scores.

Final verification: 68 backend tests and 9 frontend tests passed, plus JavaScript syntax/Python compilation checks. In the running browser, the original Urdu PDF loaded through the shared paperclip, displayed its page preview and blocked Translate pending source review. Attachment removal cleared the text and approval. The previous short source text was restored after this check. The app is running locally; no deployment or new provider translation was performed.

## Original Urdu PDF observation

The existing public KPTEVTA affidavit at tmp/evidence/kptevta-affidavit.pdf was extracted and compared with its page image. It produced 2,692 characters and recognizable Urdu, but some words were missing or reordered: for example, text around the political/religious-activity clause is jumbled, and part of the training-completion wording is omitted. It is therefore NOT an exact extraction pass. Mandatory review is necessary; this file needs correction before translation. Never automatically reverse all Urdu strings as a workaround, because that can corrupt numbers and mixed English text.

Source: https://kptevta.gov.pk/wp-content/uploads/2023/02/Affidavit-.pdf

Selectable text alone does not guarantee correct extraction. pypdf does not perform OCR; PDF text positioning and font encodings complicate reading order. Reference: https://pypdf.readthedocs.io/en/6.18.1/user/extract-text.html

## Live-service limitation

The preceding six live translation calls returned exhausted-quota responses; no new calls were made during this extraction/UI change. Live translation quality for the new PDF workflow remains unverified. Word export visual inspection is still blocked by the missing renderer. These limitations are separate from passing offline tests.
