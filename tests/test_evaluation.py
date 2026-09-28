import json
import pytest
from evaluation.run import load_cases, summarize


def test_real_case_provenance_and_direction_coverage():
    from pathlib import Path
    cases = load_cases(Path('evaluation/datasets/real-cases.jsonl'))
    assert len(cases) == 4
    assert {case['direction'] for case in cases} == {'en-ur','ur-en'}
    assert cases[0]['split'] == 'regression'
    assert '[FULL NAME]' in cases[0]['source']
    assert all(case['review_points'] for case in cases)


def test_invalid_dataset_rejected_before_live_calls(tmp_path):
    path = tmp_path/'cases.jsonl'
    record = {'id':'one','source':'Hello','direction':'xx-yy','provenance':'synthetic','split':'development','domain':'office','review_points':[]}
    path.write_text(json.dumps(record),encoding='utf-8')
    with pytest.raises(ValueError): load_cases(path)


def test_summary_counts_failures_without_calling_them_quality_failures():
    rows = [
        {'model':'m','profile':'domain','direction':'en-ur','status':'completed','warnings':[],'elapsed_ms':100},
        {'model':'m','profile':'domain','direction':'en-ur','status':'failed','elapsed_ms':90000}]
    summary = summarize(rows)
    assert '| 1 | 1 | 0 | 100 |' in summary
    assert 'not semantic accuracy' in summary


def test_source_path_cannot_escape_project(tmp_path):
    path = tmp_path/'cases.jsonl'
    path.write_text(json.dumps({'id':'bad','source_file':'../../outside.txt'}),encoding='utf-8')
    with pytest.raises(ValueError,match='inside project'): load_cases(path)
