"""Synthetic PDF parser fixtures, not model-quality or visual-layout evidence."""
from io import BytesIO
import subprocess

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject, ArrayObject, NumberObject, TextStringObject
from fastapi.testclient import TestClient
from app.main import app, get_provider
from app.upload import extract_pdf, extract_document, DocumentError
from app.translation import ModelTranslation
from docx import Document


def pdf_bytes(pages, *, image_page=None, encrypted=False, annotation=False, unicode=False):
    writer = PdfWriter()
    for index, text in enumerate(pages):
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
        if unicode:
            cmap = DecodedStreamObject()
            cmap.set_data(b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange 1 beginbfrange <0000> <FFFF> <0000> endbfrange endcmap end end')
            font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type0'),NameObject('/BaseFont'):NameObject('/Synthetic'),NameObject('/Encoding'):NameObject('/Identity-H'),NameObject('/ToUnicode'):writer._add_object(cmap)})
            descendant=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/CIDFontType2'),NameObject('/BaseFont'):NameObject('/Synthetic'),NameObject('/DW'):NumberObject(600),NameObject('/CIDSystemInfo'):DictionaryObject({NameObject('/Registry'):TextStringObject('Adobe'),NameObject('/Ordering'):TextStringObject('Identity'),NameObject('/Supplement'):NumberObject(0)})})
            font[NameObject('/DescendantFonts')]=ArrayObject([writer._add_object(descendant)])
        resources = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        if text:
            operand = '<' + text.encode('utf-16-be').hex() + '>' if unicode else '(' + text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)') + ')'
            stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 40 780 Td '+operand+' Tj ET').encode('ascii'))
            page[NameObject('/Contents')] = writer._add_object(stream)
        if image_page == index:
            image=DecodedStreamObject();image.set_data(b'\x00')
            image.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Image'),NameObject('/Width'):NumberObject(1),NameObject('/Height'):NumberObject(1),NameObject('/ColorSpace'):NameObject('/DeviceGray'),NameObject('/BitsPerComponent'):NumberObject(8)})
            resources[NameObject('/XObject')] = DictionaryObject({NameObject('/Image1'):writer._add_object(image)})
        page[NameObject('/Resources')] = resources
        if annotation:
            page[NameObject('/Annots')]=ArrayObject([DictionaryObject({NameObject('/Subtype'):NameObject('/Widget')})])
    if encrypted: writer.encrypt('test-password')
    result=BytesIO();writer.write(result);return result.getvalue()


def test_pdf_pages_boundaries_and_identifiers():
    result=extract_pdf(pdf_bytes(['Leave is not approved. CNIC: 12345-1234567-1','Pay Rs. 25,000 within 30 days.']))
    assert result['text']=='Leave is not approved. CNIC: 12345-1234567-1\n\nPay Rs. 25,000 within 30 days.'
    assert len(result['pages'])==2
    for page in result['pages']:
        assert result['text'][page['start']:page['end']]


@pytest.mark.parametrize('direction,source,target',[
    ('en-ur','Leave is not approved.','رخصت منظور نہیں ہوئی۔'),
    ('en-ur','Pay Rs. 25,000 within 30 days.','30 دن کے اندر 25,000 روپے ادا کریں۔'),
    ('ur-en','درخواست منظور نہیں ہوئی','The application has not been approved.'),
    ('ur-en','یہ بیان درست ہے','This statement is correct.'),
])
def test_pdf_to_translation_to_word(direction,source,target):
    is_urdu=direction=='ur-en'
    # Synthetic glyph-order fixture exercises the Unicode map and RTL extraction.
    data=pdf_bytes([source[::-1] if is_urdu else source], unicode=is_urdu)
    class Provider:
        async def translate(self, body):
            assert body.text==source and body.direction==direction
            return ModelTranslation(translation=target,notes=[])
    app.dependency_overrides[get_provider]=Provider
    try:
        with TestClient(app) as client:
            result=client.post('/api/upload',content=data,headers={'content-type':'application/pdf'})
            assert result.status_code==200,result.text
            assert result.json()['text']==source
            translated=client.post('/api/translate',json={'text':result.json()['text'],'direction':direction})
            assert translated.status_code==200
            exported=client.post('/api/export/docx',json={'text':translated.json()['translation'],'direction':direction,'approved':True})
            assert exported.status_code==200
            assert Document(BytesIO(exported.content)).paragraphs[0].text==target
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize('data,expected',[
    (b'not PDF','valid PDF'),
    (b'%PDF-1.7\nbroken','damaged'),
    (pdf_bytes(['Secret'],encrypted=True),'Password-protected'),
    (pdf_bytes([]),'1–10 pages'),
    (pdf_bytes(['x']*11),'1–10 pages'),
    (pdf_bytes(['']),'page 1'),
    (pdf_bytes([''],image_page=0),'images or scanned'),
    (pdf_bytes(['Readable',''],image_page=1),'page 2'),
    (pdf_bytes(['Text with a logo'],image_page=0),'No pages were imported'),
    (pdf_bytes(['Form'],annotation=True),'annotations or form'),
    (pdf_bytes(['x'*5001]),'5,000'),
    (pdf_bytes(['x'*2_000_001]),'limits'),
], ids=['wrong-magic','broken','encrypted','zero-pages','too-many-pages','blank','scan','mixed','logo','form','too-long','large-stream'])
def test_rejected_pdfs(data,expected):
    with pytest.raises(DocumentError,match=expected): extract_pdf(data)


def test_worker_timeout_is_clear(monkeypatch):
    def timeout(*args,**kwargs): raise subprocess.TimeoutExpired('parser',12)
    monkeypatch.setattr(subprocess,'run',timeout)
    with pytest.raises(DocumentError,match='timed out'): extract_document(b'%PDF-','pdf')


def test_pdf_limits_and_mismatched_content_via_api():
    with TestClient(app) as client:
        assert client.post('/api/upload',content=b'x'*(2*1024*1024+1),headers={'content-type':'application/pdf'}).status_code==413
        assert client.post('/api/upload',content=b'PKfake',headers={'content-type':'application/pdf'}).status_code==422
        assert client.post('/api/upload',content=pdf_bytes(['Hello']),headers={'content-type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}).status_code==422
        assert client.post('/api/upload',content=b'',headers={'content-type':'application/pdf'}).status_code==422
