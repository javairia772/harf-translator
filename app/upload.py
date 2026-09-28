"""Bounded document extraction; file content is never executed or sent to a model."""
from io import BytesIO
from zipfile import ZipFile, BadZipFile
from lxml import etree
import json
import os
from pathlib import Path
import subprocess
import sys
import unicodedata

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
MIME_TYPES = {'application/pdf':'pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':'docx'}


class DocumentError(ValueError):
    pass


def extracted_result(text, warnings, **metadata):
    if not text.strip():
        raise DocumentError('No readable text found. Scanned documents need OCR, which is not supported yet.')
    if len(text) > 5000:
        raise DocumentError('The document exceeds 5,000 characters. Split it into smaller documents; no text has been truncated.')
    if any((unicodedata.category(c) in ('Cc', 'Cs', 'Co') and c not in '\n\t\r') or c == '\ufffd' for c in text):
        raise DocumentError('The extracted text contains unreadable characters. Paste a checked copy of the text instead.')
    return {'text':text, 'warnings':warnings, 'characters':len(text), **metadata}


def extract_docx(data: bytes) -> dict:
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError('Word files must be at most 2 MiB.')
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if len(entries) > 256 or len(set(names)) != len(names) or sum(item.file_size for item in entries) > 8 * 1024 * 1024:
                raise DocumentError('This document is too complex or expands beyond the supported size.')
            if 'word/document.xml' not in names or '[Content_Types].xml' not in names:
                raise DocumentError('Upload a valid .docx Word document.')
            if any('vbaproject' in name.lower() or name.startswith('word/embeddings/') for name in names):
                raise DocumentError('Macros and embedded files are not supported. Paste the required text instead.')
            roots = {}
            for name in names:
                if name.endswith('.xml'):
                    raw = archive.read(name)
                    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
                    root = etree.fromstring(raw, parser)
                    if root.getroottree().docinfo.doctype:
                        raise DocumentError('Document XML declarations are not supported.')
                    roots[name] = root
            content_types = archive.read('[Content_Types].xml')
            if b'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml' not in content_types:
                raise DocumentError('Only standard .docx documents are supported.')
            root = roots['word/document.xml']
            # Reject features that could silently omit or change visible legal/office text.
            unsupported = {'drawing','pict','object','altChunk','ins','del','moveFrom','moveTo',
                           'fldChar','fldSimple','instrText','numPr','sdt','vMerge','gridSpan','vanish',
                           'sym','AlternateContent','footnoteReference','endnoteReference'}
            if any(etree.QName(node).localname in unsupported for node in root.iter()):
                raise DocumentError('This file contains images, fields, revisions, numbering or complex formatting that cannot be extracted reliably. Paste the relevant text, or upload a plain-text Word copy.')
            styles_root = roots.get('word/styles.xml')
            if styles_root is not None:
                styles = {s.get(W+'styleId'):s for s in styles_root.findall(W+'style')}
                referenced = {n.get(W+'val') for n in root.iter() if n.tag in (W+'pStyle', W+'rStyle')}
                referenced.update(s.get(W+'styleId') for s in styles.values() if s.get(W+'default') == '1')
                checked = set()
                while referenced:
                    name = referenced.pop()
                    if name in checked or name not in styles:
                        continue
                    checked.add(name)
                    style = styles[name]
                    if any(n.tag in (W+'numPr', W+'vanish') for n in style.iter()):
                        raise DocumentError('This document uses numbering or hidden text through styles. Paste the required text instead.')
                    parent = style.find(W+'basedOn')
                    if parent is not None:
                        referenced.add(parent.get(W+'val'))
            for name, other in roots.items():
                if name.startswith(('word/header','word/footer','word/footnotes','word/endnotes','word/comments')) and any((node.text or '').strip() for node in other.iter(W+'t')):
                    raise DocumentError('This file has header, footer, note or comment text. Paste all required text into the source pane to avoid omissions.')
            body = root.find(W+'body')
            if body is None:
                raise DocumentError('The document has no readable body.')
            def paragraph(node):
                parts = []
                for item in node.iter():
                    if item.tag == W+'t': parts.append(item.text or '')
                    elif item.tag == W+'tab': parts.append('\t')
                    elif item.tag in (W+'br', W+'cr'): parts.append('\n')
                return ''.join(parts)
            lines, tables = [], False
            for block in body:
                if block.tag == W+'p':
                    lines.append(paragraph(block))
                elif block.tag == W+'tbl':
                    tables = True
                    if len(list(block.iter(W+'tbl'))) > 1:
                        raise DocumentError('Nested tables are not supported. Paste the required text instead.')
                    for row in block.findall(W+'tr'):
                        lines.append('\t'.join('\n'.join(paragraph(p) for p in cell.findall(W+'p')) for cell in row.findall(W+'tc')))
                elif block.tag != W+'sectPr':
                    raise DocumentError('Unsupported document structure. Paste the required text instead.')
            text = '\n'.join(lines)
            warnings = ['Review the extracted text before translating. Original layout is not preserved.']
            if tables:
                warnings.append('Tables were converted to rows of text with tab-separated cells. Check the reading order.')
            return extracted_result(text, warnings, format='docx')
    except DocumentError:
        raise
    except (BadZipFile, etree.XMLSyntaxError, KeyError, ValueError, RuntimeError, NotImplementedError, OSError):
        raise DocumentError('The file is damaged, encrypted or not a supported .docx document.') from None


def extract_pdf(data: bytes) -> dict:
    from pypdf import PdfReader, Configuration, apply_configuration
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError('PDF files must be at most 2 MiB.')
    if not data.startswith(b'%PDF-'):
        raise DocumentError('The file is not a valid PDF.')
    configuration = Configuration(maximum_declared_stream_length=2_000_000,
        array_based_stream_maximum_output_length=2_000_000, zlib_maximum_output_length=2_000_000,
        lzw_maximum_output_length=2_000_000, run_length_maximum_output_length=2_000_000,
        page_tree_maximum_entries=100, page_tree_maximum_depth=20,
        xform_maximum_invocations_per_extraction=100, jbig2dec_binary=None)
    try:
        with apply_configuration(configuration):
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise DocumentError('Password-protected PDFs are not supported. Upload an unlocked copy.')
            if not 1 <= len(reader.pages) <= 10:
                raise DocumentError('PDFs must contain 1–10 pages. Split larger files before uploading.')
            if reader.root_object.get('/AcroForm'):
                raise DocumentError('Interactive PDF forms are not supported. Paste the completed text to avoid missing field values.')
            parts, problems, page_ranges = [], [], []
            def inspect_resources(resources, seen=None):
                seen = set() if seen is None else seen
                if not resources:
                    return False
                resources = resources.get_object()
                objects = resources.get('/XObject', {})
                objects = objects.get_object() if hasattr(objects, 'get_object') else objects
                for reference in objects.values():
                    obj = reference.get_object()
                    if obj.get('/Subtype') == '/Image':
                        return True
                    if obj.get('/Subtype') == '/Form':
                        if id(obj) in seen or len(seen) >= 20:
                            raise DocumentError('PDF graphics are too complex to extract reliably.')
                        seen.add(id(obj))
                        stream = obj.get_data()
                        if len(stream) > 2_000_000 or b'BI' in stream:
                            return True
                        if inspect_resources(obj.get('/Resources'), seen):
                            return True
                return False
            for number, page in enumerate(reader.pages, 1):
                if page.get('/Annots') and any(a.get_object().get('/Subtype') != '/Link' for a in page['/Annots']):
                    problems.append(f'page {number}: annotations or form values'); continue
                if inspect_resources(page.get('/Resources')):
                    problems.append(f'page {number}: images or scanned content'); continue
                contents = page.get_contents()
                if contents is not None:
                    if len(contents.get_data()) > 2_000_000:
                        raise DocumentError('A PDF page is too complex to extract safely.')
                    if any(operator == b'INLINE IMAGE' for _, operator in contents.operations):
                        problems.append(f'page {number}: inline images'); continue
                value = (page.extract_text() or '').strip()
                if not value:
                    problems.append(f'page {number}: no readable text (blank, scanned or outlined text)'); continue
                start = sum(len(p) for p in parts) + 2 * len(parts)
                page_ranges.append({'page':number, 'start':start, 'end':start + len(value)})
                parts.append(value)
                if sum(len(p) for p in parts) + 2 * (len(parts)-1) > 5000:
                    raise DocumentError('The document exceeds 5,000 characters. Split it into smaller documents; no text has been truncated.')
            if problems:
                raise DocumentError('Cannot extract the complete PDF: ' + '; '.join(problems) + '. OCR/image interpretation is not included. Upload a text-only copy or paste all required text. No pages were imported.')
            return extracted_result('\n\n'.join(parts), [
                'Review every page against the original PDF, especially Urdu reading order, tables, names and numbers. Extraction is not proof of completeness.',
                'Pages are separated by a blank line. Original layout is not preserved.'],
                format='pdf', pages=page_ranges)
    except DocumentError:
        raise
    except Exception:
        # Parser internals can contain document text; do not expose them.
        raise DocumentError('The PDF is damaged, unreadable or exceeds extraction limits. Try a simpler text-only PDF.') from None


def extract_document(data: bytes, kind: str) -> dict:
    if kind not in ('pdf', 'docx') or len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError('Choose a Word or PDF file no larger than 2 MiB.')
    # A subprocess can be terminated on timeout, unlike a thread running a parser.
    # Pass only runtime environment values; provider credentials are unnecessary here.
    environment = {key:value for key,value in os.environ.items() if key.upper() in
        ('SYSTEMROOT','WINDIR','PATH','TEMP','TMP','LANG','LC_ALL')}
    try:
        result = subprocess.run([sys.executable, '-m', 'app.upload', kind], input=data,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12,
            cwd=Path(__file__).resolve().parent.parent, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode != 0:
            raise DocumentError('This document could not be read within the extraction limits.')
        payload = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise DocumentError('Reading the document timed out. Use a smaller or simpler file; your existing text is unchanged.') from None
    except (OSError, ValueError):
        raise DocumentError('Document extraction is unavailable. Your existing text is unchanged.') from None
    if 'error' in payload:
        raise DocumentError(payload['error'])
    return payload


if __name__ == '__main__':
    # The Linux deployment also caps parser memory. Windows relies on file/stream
    # limits and process timeout; no hard per-process memory cap is claimed there.
    if sys.platform == 'linux':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (256*1024*1024, 256*1024*1024))
    try:
        raw = sys.stdin.buffer.read(MAX_UPLOAD_BYTES + 1)
        result = extract_pdf(raw) if sys.argv[1] == 'pdf' else extract_docx(raw)
    except Exception as error:
        result = {'error': str(error) if isinstance(error, DocumentError) else 'The document could not be read safely.'}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
