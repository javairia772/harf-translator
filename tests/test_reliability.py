import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import app, get_provider
from app.reliability import ProviderError, exhausted_quota, retry_delay, with_retries
from app.translation import GeminiProvider, ModelTranslation, TranslationRequest, acronyms, fidelity_warnings, placeholders


def test_known_headings_and_placeholder_labels_do_not_mask_real_acronyms():
    source = '# DECLARATION / AFFIDAVIT\n## FOR ISSUANCE OF NO OBJECTION CERTIFICATE (NOC)\n**DEPONENT**\nCNIC [FULL NAME] PFA ABC'
    assert acronyms(source) == {'CNIC', 'NOC', 'PFA', 'ABC'}
    assert fidelity_warnings(source, 'حلف نامہ NOC CNIC PFA ABC [FULL NAME]') == []


def test_duplicate_and_case_changed_placeholders_are_flagged():
    assert placeholders('[FULL NAME] [FULL NAME]')['[FULL NAME]'] == 2
    assert fidelity_warnings('[FULL NAME] [FULL NAME]', '[FULL NAME] [Full Name]')


def test_missing_clause_is_flagged_even_when_no_numbers_changed():
    assert any('clauses' in x for x in fidelity_warnings('1. First\n2. Second', '1 اور 2'))
    assert not fidelity_warnings('1. First\n2. Second', '۱۔ پہلا\n۲۔ دوسرا')


def test_retry_then_success_and_progress_without_real_wait():
    calls, events, waits, metadata = [], [], [], {}
    async def operation():
        calls.append(1)
        if len(calls) < 3:
            raise ProviderError('busy', retryable=True, code='upstream_503')
        return 'translated'
    async def sleep(delay): waits.append(delay)
    result = asyncio.run(with_retries(operation, progress=events.append, metadata=metadata, sleep=sleep, jitter=lambda:0))
    assert result == 'translated'
    assert waits == [2,4]
    assert metadata['attempts'] == 3
    assert [e['attempt'] for e in events if e['state']=='retrying'] == [2,3]


def test_permanent_error_is_never_retried():
    calls = []
    async def operation():
        calls.append(1)
        raise ProviderError('bad key')
    with pytest.raises(ProviderError): asyncio.run(with_retries(operation))
    assert len(calls) == 1


def test_max_attempts_and_retry_after_limit():
    calls = []
    async def operation():
        calls.append(1)
        raise ProviderError('busy', retryable=True)
    async def sleep(delay): pass
    with pytest.raises(ProviderError): asyncio.run(with_retries(operation,sleep=sleep))
    assert len(calls) == 3
    async def long_wait(): raise ProviderError('rate limit',429,retryable=True,retry_after=120)
    with pytest.raises(ProviderError) as error:
        asyncio.run(with_retries(long_wait,sleep=sleep))
    assert error.value.code == 'retry_wait_exceeds_deadline'


def test_overall_deadline_cancels_request():
    cancelled = []
    async def operation():
        try: await asyncio.sleep(1)
        finally: cancelled.append(True)
    with pytest.raises(ProviderError) as error:
        asyncio.run(with_retries(operation,deadline=.01))
    assert error.value.code == 'deadline'
    assert cancelled


def test_retry_after_and_daily_quota_classification():
    response = httpx.Response(429,headers={'Retry-After':'12'},json={})
    assert retry_delay(response) == 12
    assert retry_delay(httpx.Response(429,json={'error':{'details':[{'retryDelay':'4s'}]}})) == 4
    assert exhausted_quota(httpx.Response(429,json={'error':{'details':[{'violations':[{'quotaId':'GenerateRequestsPerDay'}]}]}}))
    assert not exhausted_quota(httpx.Response(429,json={'error':{'message':'per minute limit'}}))
    assert not exhausted_quota(httpx.Response(429,json={'error':{'message':'Quota exceeded, please check your plan and billing details.'}}))


def test_protected_field_change_rejected(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','test-only')
    monkeypatch.setenv('GEMINI_MODEL','test-model')
    def handler(request):
        return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{
            'text':json.dumps({'translation':'[مکمل نام]', 'notes':[]})}]}}]})
    with pytest.raises(ProviderError) as error:
        asyncio.run(GeminiProvider(httpx.MockTransport(handler),max_attempts=1).translate(TranslationRequest(text='[FULL NAME]',direction='en-ur')))
    assert error.value.code == 'invalid_output'


def test_literal_escaped_newline_is_rejected(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','test-only')
    monkeypatch.setenv('GEMINI_MODEL','test-model')
    def handler(request):
        return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{
            'text':json.dumps({'translation':'پہلی سطر\\nدوسری سطر', 'notes':[]})}]}}]})
    with pytest.raises(ProviderError) as error:
        asyncio.run(GeminiProvider(httpx.MockTransport(handler),max_attempts=1).translate(TranslationRequest(text='First line\nSecond line',direction='en-ur')))
    assert error.value.code == 'invalid_output'


def test_stream_reports_progress_and_result():
    class Provider:
        async def translate(self, request, progress):
            progress({'type':'progress','state':'retrying','attempt':2,'max_attempts':3,'wait_seconds':2})
            return ModelTranslation(translation='درخواست',notes=[])
    app.dependency_overrides[get_provider] = Provider
    try:
        with TestClient(app) as client:
            response = client.post('/api/translate/stream',json={'text':'application','direction':'en-ur'})
        events = [json.loads(line) for line in response.text.splitlines()]
        assert [event['type'] for event in events] == ['progress','result']
        assert events[-1]['translation'] == 'درخواست'
    finally: app.dependency_overrides.clear()


def test_stream_has_safe_terminal_error():
    class Provider:
        async def translate(self, request, progress):
            raise RuntimeError('secret-key-and-source')
    app.dependency_overrides[get_provider] = Provider
    try:
        with TestClient(app) as client:
            response = client.post('/api/translate/stream',json={'text':'application','direction':'en-ur'})
        assert 'secret-key-and-source' not in response.text
        assert json.loads(response.text)['type'] == 'error'
    finally: app.dependency_overrides.clear()
