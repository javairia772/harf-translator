const $ = id => document.getElementById(id);
let configured = false;
let busy = false;
let sourceVersion = 0;
let outputVersion = -1;
let approvedSnapshot = null;
let uploadReviewRequired = false;
let sourceType = 'paste';
let feedbackEnabled = false;
let currentWorkflowId = null;
let originalTranslation = '';
let translatedSource = '';
let translationReadyAt = 0;
function clearAttachment() {
  $('attachment-name').textContent = ''; $('remove-attachment').hidden = true;
  $('upload-state').textContent = ''; $('page-preview').replaceChildren(); $('page-preview').hidden = true;
}
function snapshot() { return JSON.stringify([$('source').value, $('direction').value, $('output').value]); }
function revokeApproval() {
  approvedSnapshot = null; $('approve').checked = false;
  $('approval-state').textContent = 'Review the current translation before approving. Editing clears approval.';
}

function message(text, error = false) {
  $('message').textContent = text;
  $('message').classList.toggle('error', error);
}
function buttons() {
  $('translate').disabled = busy || !configured || !$('source').value.trim();
  if (uploadReviewRequired && !$('source-reviewed').checked) $('translate').disabled = true;
  $('source-reviewed').disabled = busy;
  $('copy').disabled = busy || !$('output').value.trim() || outputVersion !== sourceVersion;
  $('approve').disabled = $('copy').disabled;
  $('download').disabled = $('copy').disabled || approvedSnapshot !== snapshot();
  $('direction').disabled = busy;
  $('document').disabled = busy;
  $('attach').disabled = busy;
  $('remove-attachment').disabled = busy;
  $('clear').disabled = busy;
  $('source').readOnly = busy;
  $('output').readOnly = busy;
}
function changedSource() {
  revokeApproval(); currentWorkflowId = null; $('feedback').hidden = true;
  sourceVersion++;
  $('counter').textContent = `${$('source').value.length.toLocaleString()} / 5,000`;
  $('notes').hidden = true;
  if ($('output').value) $('output-state').textContent = 'Source changed. Translate again to update this result.';
  message(''); buttons();
}
$('source').addEventListener('input', () => { clearAttachment(); $('source-reviewed').checked = false; changedSource(); });
$('source-reviewed').addEventListener('change', buttons);
$('direction').addEventListener('change', () => {
  const toUrdu = $('direction').value === 'en-ur';
  $('source').lang = toUrdu ? 'en' : 'ur'; $('source').dir = toUrdu ? 'ltr' : 'rtl';
  $('source-label').textContent = toUrdu ? 'English source' : 'Urdu source';
  $('output-label').textContent = toUrdu ? 'Urdu translation' : 'English translation';
  $('output').lang = toUrdu ? 'ur' : 'en'; $('output').dir = toUrdu ? 'rtl' : 'ltr';
  $('output').placeholder = toUrdu ? 'ترجمہ یہاں ظاہر ہوگا' : 'Your translation will appear here…';
  $('output').value = ''; $('output-state').textContent = 'Your translation will appear here.';
  changedSource();
});
$('output').addEventListener('input', () => {
  revokeApproval();
  $('notes').hidden = true;
  if (selectedRating() && selectedRating() !== 'good') $('feedback-consent').hidden = $('output').value === originalTranslation;
  $('output-state').textContent = outputVersion === sourceVersion ? 'Edited by you. Review changes against the source.' : 'Source changed. Translate again to update this result.';
  buttons();
});
$('clear').addEventListener('click', () => {
  clearAttachment();
  uploadReviewRequired = false; sourceType = 'paste'; $('source-review').hidden = true; $('source-reviewed').checked = false;
  $('source').value = ''; $('output').value = ''; $('output-state').textContent = 'Your translation will appear here.';
  changedSource(); $('source').focus();
});
$('copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('output').value); message('Translation copied.'); }
  catch { message('Copy was unavailable. Select the translation and copy it manually.', true); }
});
$('translator').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || !configured || !$('source').value.trim()) return;
  if (uploadReviewRequired && !$('source-reviewed').checked) { message('Check the extracted text against your document and confirm it before translating.', true); return; }
  if ($('output').value && !window.confirm('Replace the current translation, including your edits?')) return;
  revokeApproval(); currentWorkflowId = null; $('feedback').hidden = true; busy = true; buttons(); message('Translating…'); $('notes').hidden = true;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 95000);
  try {
    const response = await fetch('/api/translate/stream', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:$('source').value, direction:$('direction').value, source_type:sourceType, source_reviewed:!uploadReviewRequired || $('source-reviewed').checked}), signal:controller.signal});
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Translation failed. Please try again.');
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '', result = null;
    function accept(line) {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (event.type === 'error') throw new Error(event.detail);
      if (event.type === 'result') result = event;
      if (event.type === 'progress') {
        message(event.state === 'retrying'
          ? `The previous attempt could not complete. Retrying in ${event.wait_seconds}s — attempt ${event.attempt} of ${event.max_attempts}.`
          : `Translating — attempt ${event.attempt} of ${event.max_attempts}…`);
      }
    }
    try {
      while (true) {
        const {done, value} = await reader.read();
        buffer += decoder.decode(value, {stream: !done});
        const lines = buffer.split('\n'); buffer = lines.pop();
        for (const line of lines) accept(line);
        if (done) {accept(buffer); break;}
      }
    } finally { await reader.cancel().catch(() => {}); }
    if (!result) throw new Error('The connection ended before translation completed. Your text is unchanged.');
    $('output').value = result.translation; outputVersion = sourceVersion;
    currentWorkflowId = result.workflow_id || null; originalTranslation = result.translation;
    translatedSource = $('source').value; translationReadyAt = Date.now();
    $('feedback').hidden = !feedbackEnabled || !currentWorkflowId; $('feedback-state').textContent = '';
    $('output-state').textContent = 'Draft translation · review before use';
    $('notes-list').replaceChildren();
    const notes = [...result.warnings, ...result.notes.map(note => `${note.source_span} — ${note.reason}`)];
    for (const note of notes) { const li = document.createElement('li'); li.textContent = note; $('notes-list').append(li); }
    $('notes').hidden = !notes.length;
    message('Translation ready for review.');
  } catch (error) {
    message(error.name === 'AbortError' ? 'Translation timed out. Your text is unchanged.' : error.message, true);
  } finally {clearTimeout(timer); busy = false; buttons();}
});
async function initialize() {
  buttons();
  try {
    const response = await fetch('/api/status');
    if (!response.ok) throw new Error();
    const status = await response.json(); configured = status.configured; feedbackEnabled = !!status.feedback_enabled;
    $('connection').textContent = configured ? 'Translation connection configured. Use a short sample to check it.' : 'Translation is not connected yet. Set GEMINI_API_KEY and GEMINI_MODEL in the local .env file, then restart the server.';
  } catch { $('connection').textContent = 'Cannot connect to the local server. Refresh after it is running.'; }
  buttons();
}
initialize();

$('attach').addEventListener('click', () => { if (!busy) $('document').click(); });
$('remove-attachment').addEventListener('click', () => {
  if (busy || !window.confirm('Remove this file and clear its source text, translation and approval?')) return;
  $('clear').click();
});

$('document').addEventListener('change', async () => {
  const file = $('document').files[0];
  if (!file || busy) return;
  const extension = file.name.toLowerCase().split('.').pop();
  if (!['docx','pdf'].includes(extension) || !file.size || file.size > 2 * 1024 * 1024) {
    message('Choose a nonempty Word (.docx) or PDF file no larger than 2 MiB.', true); $('document').value = ''; return;
  }
  if (($('source').value || $('output').value) && !window.confirm('Replace the current source and translation with text from this document?')) {
    $('document').value = ''; return;
  }
  busy = true; buttons(); message('Reading document…');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const mime = extension === 'pdf' ? 'application/pdf' : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
    const response = await fetch('/api/upload', {method:'POST', headers:{'Content-Type':mime}, body:file, signal:controller.signal});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Could not read this document.');
    clearAttachment();
    $('source').value = result.text; $('output').value = ''; outputVersion = -1;
    uploadReviewRequired = true; sourceType = extension; $('source-reviewed').checked = false; $('source-review').hidden = false;
    $('output-state').textContent = 'Review the extracted source, choose the direction, then translate.';
    changedSource();
    $('attachment-name').textContent = file.name; $('remove-attachment').hidden = false;
    $('upload-state').textContent = `${file.name}: ${result.characters} characters extracted. ${result.warnings.join(' ')}`;
    for (const page of result.pages || []) {
      const details = document.createElement('details'), summary = document.createElement('summary'), preview = document.createElement('pre');
      summary.textContent = `Page ${page.page} extracted text`;
      preview.textContent = Array.from(result.text).slice(page.start, page.end).join(''); preview.dir = 'auto';
      details.append(summary, preview); $('page-preview').append(details);
    }
    $('page-preview').hidden = !(result.pages || []).length;
    message('Document loaded. Check the extracted text and translation direction before translating.');
  } catch (error) { message(error.name === 'AbortError' ? 'Upload timed out. Your existing text is unchanged.' : error.message, true); }
  finally { clearTimeout(timer); busy = false; $('document').value = ''; buttons(); }
});

$('approve').addEventListener('change', () => {
  approvedSnapshot = $('approve').checked && !$('approve').disabled ? snapshot() : null;
  $('approval-state').textContent = approvedSnapshot ? 'Current text approved by you. Ready to download.' : 'Approval cleared.';
  buttons();
});
$('download').addEventListener('click', async () => {
  if (busy || approvedSnapshot !== snapshot() || $('download').disabled) return;
  const payload = {text:$('output').value, direction:$('direction').value, approved:true};
  if (currentWorkflowId) payload.workflow_id = currentWorkflowId;
  busy = true; buttons();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch('/api/export/docx', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload), signal:controller.signal});
    if (!response.ok) throw new Error('Word download failed. Your text is still here; please try again.');
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a'); link.href = url; link.download = 'reviewed-translation.docx';
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message('Word document downloaded with your reviewed text.');
  } catch (error) { message(error.name === 'AbortError' ? 'Download timed out. Your text is unchanged.' : error.message, true); }
  finally { clearTimeout(timer); busy = false; buttons(); }
});

const ratingIds = ['rating-good','rating-corrected','rating-unusable'];
const categoryNames = ['meaning','terminology','name','number','formatting','fluency','other'];
function selectedRating() {
  for (const id of ratingIds) if ($(id).checked) return $(id).value;
  return null;
}
for (const id of ratingIds) $(id).addEventListener('change', () => {
  const needsDetail = selectedRating() !== 'good';
  $('feedback-categories').hidden = !needsDetail;
  $('feedback-consent').hidden = !needsDetail || $('output').value === originalTranslation;
});
$('submit-feedback').addEventListener('click', async () => {
  const rating = selectedRating();
  if (!rating) { $('feedback-state').textContent = 'Choose a rating first.'; return; }
  const categories = categoryNames.filter(name => $(`category-${name}`).checked);
  if (rating !== 'good' && !categories.length) { $('feedback-state').textContent = 'Choose what needed correction.'; return; }
  const consent = $('store-example').checked;
  const payload = {
    workflow_id: currentWorkflowId, rating, categories,
    was_edited: $('output').value !== originalTranslation,
    correction_seconds: Math.min(86400, Math.max(0, Math.round((Date.now() - translationReadyAt) / 1000))),
    consent_to_store_text: consent
  };
  if (consent) {
    payload.source_text = translatedSource;
    payload.original_translation = originalTranslation;
    payload.corrected_translation = $('output').value;
  }
  $('submit-feedback').disabled = true; $('feedback-state').textContent = 'Saving feedback…';
  try {
    const response = await fetch('/api/feedback', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Feedback could not be saved.');
    $('feedback-state').textContent = 'Thank you. Your feedback was saved.';
  } catch (error) {
    $('feedback-state').textContent = error.message;
    $('submit-feedback').disabled = false;
  }
});
