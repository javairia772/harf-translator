"""Reusable opt-in live evaluator: data changes per case; this runner does not.

No gold accuracy scores are computed. Automated checks and failures are recorded
separately from human judgments. Runs are append-only in a new output directory.
"""
import argparse
import asyncio
import hashlib
import html
import json
import os
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from dotenv import load_dotenv
from app.translation import GeminiProvider, ModelTranslation, TranslationRequest, configuration, fidelity_warnings, system_prompt
from app.reliability import ProviderError, with_retries
from app.export import word_document
from app.upload import extract_docx
from docx import Document
from io import BytesIO


def load_cases(path):
    records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    seen = set()
    for case in records:
        if case['id'] in seen:
            raise ValueError('Duplicate case ID')
        seen.add(case['id'])
        if 'source_file' in case:
            target = (ROOT / case['source_file']).resolve()
            if not target.is_relative_to(ROOT):
                raise ValueError('Source file must be inside project')
            case['source'] = target.read_text(encoding='utf-8')
        TranslationRequest(text=case['source'], direction=case['direction'])
        for field in ('provenance', 'split', 'domain', 'review_points'):
            if field not in case:
                raise ValueError(f'Missing {field}')
    return records


class GoogleNMT:
    """Optional dedicated service baseline. Requires a separately enabled API key."""
    def __init__(self):
        self.metadata = {'model':'google-cloud-translation-nmt', 'profile':'nmt', 'usage_per_attempt':[]}

    async def translate(self, request):
        async def once():
            key = os.getenv('GOOGLE_TRANSLATE_API_KEY', '').strip()
            if not key:
                raise ProviderError('Set GOOGLE_TRANSLATE_API_KEY for the dedicated translation baseline.', 503)
            source, target = request.direction.split('-')
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post('https://translation.googleapis.com/language/translate/v2',
                        headers={'X-Goog-Api-Key':key}, json={'q':request.text, 'source':source, 'target':target, 'format':'text', 'model':'nmt'})
            except httpx.RequestError:
                raise ProviderError('Dedicated translation service connection failed.', retryable=True, code='network') from None
            if response.is_error:
                # Fail quota/auth explicitly; never blindly retry a billing problem.
                raise ProviderError(f'Dedicated translation service returned HTTP {response.status_code}.',
                                    retryable=response.status_code >= 500, code=f'upstream_{response.status_code}')
            try:
                translation = response.json()['data']['translations'][0]['translatedText']
                self.metadata['usage_per_attempt'].append({'source_characters':len(request.text)})
                return ModelTranslation(translation=html.unescape(translation), notes=[])
            except (KeyError, TypeError, IndexError, ValueError):
                raise ProviderError('Invalid dedicated-service response.', retryable=True, code='invalid_output') from None
        return await with_retries(once, metadata=self.metadata)


def summarize(rows):
    lines = ['# Translation evaluation run', '',
             'Automated checks are diagnostics, not semantic accuracy. Review every translation independently.', '',
             '| Model / profile / direction | Completed | Failed | Outputs with warnings | Median ms (successful only) |',
             '|---|---:|---:|---:|---:|']
    groups = sorted(set((r['model'], r['profile'], r['direction']) for r in rows))
    for model, profile, direction in groups:
        selected = [r for r in rows if (r['model'],r['profile'],r['direction']) == (model,profile,direction)]
        ok = [r for r in selected if r['status'] == 'completed']
        latency = round(statistics.median(r['elapsed_ms'] for r in ok)) if ok else 'N/A'
        lines.append(f'| {model} / {profile} / {direction} | {len(ok)} | {len(selected)-len(ok)} | {sum(bool(r["warnings"]) for r in ok)} | {latency} |')
    lines += ['', 'No costs inferred from token counts. Provider pricing and failed-attempt billing require separate verification.',
              'No independent human scores are present until completed in review.jsonl. An empty warning list is not a pass.']
    return '\n'.join(lines) + '\n'


def make_review_packets(rows, out):
    completed = [row for row in rows if row['status']=='completed']
    random.Random(42).shuffle(completed)
    mapping = []
    with (out/'blinded-review.jsonl').open('w',encoding='utf-8') as handle:
        for index, row in enumerate(completed,1):
            candidate = f'CANDIDATE-{index:03}'
            mapping.append({'candidate':candidate, 'id':row['id'], 'model':row['model'], 'profile':row['profile']})
            handle.write(json.dumps({'candidate':candidate, 'source':row['source'], 'direction':row['direction'],
                'translation':row['translation'], 'review_points':row['review_points'],
                'reviewer':None, 'critical_errors':None, 'meaning_1_to_5':None,
                'terminology_1_to_5':None, 'fluency_1_to_5':None, 'correction_seconds':None, 'notes':None},ensure_ascii=False)+'\n')
    (out/'reviewer-mapping-private.json').write_text(json.dumps(mapping,indent=2),encoding='utf-8')


async def run(args, cases, out):
    rows = []
    models = args.models or [configuration()[1]]
    variants = [('gemini', model, profile) for model in models for profile in args.profiles]
    if args.include_google:
        variants.append(('google','google-cloud-translation-nmt','nmt'))
    (out/'manifest.json').write_text(json.dumps({'started':datetime.now(timezone.utc).isoformat(),
        'variants':variants,'case_ids':[case['id'] for case in cases],
        'prompts':{profile:system_prompt(profile) for profile in args.profiles},
        'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},ensure_ascii=False,indent=2),encoding='utf-8')
    for case in cases:
        # Alternate order to reduce fixed-order/provider-load bias. Still not a blinded human study.
        ordered = variants if len(rows) // len(variants) % 2 == 0 else list(reversed(variants))
        for engine, model, profile in ordered:
            provider = GoogleNMT() if engine == 'google' else GeminiProvider(model=model, profile=profile)
            row = {**case, 'model':model, 'profile':profile,
                   'timestamp':datetime.now(timezone.utc).isoformat(),
                   'source_sha256':hashlib.sha256(case['source'].encode()).hexdigest()}
            start = perf_counter()
            try:
                source = case['source']
                if getattr(args, 'docx_workflow', False):
                    source_doc = word_document(source, 'ur-en' if case['direction']=='en-ur' else 'en-ur')
                    source = extract_docx(source_doc)['text']
                    if source != case['source']:
                        raise ValueError('DOCX extraction did not preserve the source')
                    row['docx_extraction_exact'] = True
                result = await provider.translate(TranslationRequest(text=source, direction=case['direction']))
                row.update(status='completed', **result.model_dump(), warnings=fidelity_warnings(case['source'], result.translation))
                if getattr(args, 'docx_workflow', False):
                    exported = word_document(result.translation, case['direction'])
                    readback = '\n'.join(p.text for p in Document(BytesIO(exported)).paragraphs)
                    row['docx_export_exact'] = readback == result.translation.replace('\r\n','\n').replace('\r','\n')
                    # Automated readback is not human approval or visual validation.
                    row['docx_visual_review'] = 'not performed'
            except ProviderError as error:
                row.update(status='failed', error=error.message, error_code=error.code)
            row.update(elapsed_ms=round((perf_counter()-start)*1000), metadata=provider.metadata)
            rows.append(row)
            with (out / 'outputs.jsonl').open('a',encoding='utf-8') as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
            (out / 'summary.md').write_text(summarize(rows),encoding='utf-8')
            print(case['id'], model, profile, row['status'], row['elapsed_ms'], flush=True)
    with (out / 'review.jsonl').open('w',encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps({'id':row['id'], 'model':row['model'], 'profile':row['profile'],
                'reviewer':None, 'critical_errors':None, 'meaning_1_to_5':None,
                'terminology_1_to_5':None, 'fluency_1_to_5':None, 'correction_seconds':None,
                'notes':None},ensure_ascii=False)+'\n')
    make_review_packets(rows, out)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--profiles', nargs='+', choices=['basic','domain'], default=['basic','domain'])
    parser.add_argument('--models', nargs='+')
    parser.add_argument('--include-google', action='store_true')
    parser.add_argument('--limit', type=int, default=4)
    parser.add_argument('--live', action='store_true', help='Explicitly send dataset text to configured providers; may incur charges.')
    parser.add_argument('--docx-workflow', action='store_true', help='Check DOCX extraction before translation and export readback after it; does not simulate human approval.')
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('--limit must be positive')
    load_dotenv(ROOT / '.env')
    cases = load_cases(args.dataset)[:args.limit]
    count = len(cases) * (len(args.profiles)*len(args.models or [1]) + int(args.include_google))
    print(f'{len(cases)} cases; {count} evaluations; maximum {count*3} provider attempts; 90s deadline each. No automatic model switching.')
    if not args.live:
        print('Dry run only. Add --live to call the providers.'); return
    out = args.output or ROOT / 'evaluation-results' / datetime.now().strftime('%Y%m%d-%H%M%S')
    out.mkdir(parents=True, exist_ok=False)
    rows = asyncio.run(run(args, cases, out))
    if any(row['status']=='failed' for row in rows):
        print('Some evaluations failed operationally. Results saved; do not count them as scored translations.')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
