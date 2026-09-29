const $=id=>document.getElementById(id);
const show=(value,empty='None')=>value===null||value===undefined?empty:value.toLocaleString?.()??value;
function pairs(object){const root=document.createElement('div');for(const [key,value] of Object.entries(object||{})){const p=document.createElement('p');p.textContent=`${key}: ${value}`;root.append(p)}if(!root.children.length)root.textContent='No records';return root}
async function load(){ $('state').textContent='Loading telemetry…';try{const response=await fetch(`/api/admin/summary?hours=${$('window').value}`);const data=await response.json();if(!response.ok)throw new Error(data.detail||'Could not load telemetry.');
const cards=[['Translations',data.translations],['Success rate',data.success_rate===null?'—':`${data.success_rate}%`],['Provider attempts',data.provider_attempts],['With review warnings',data.translations_with_warnings],['Reported tokens',data.total_tokens_reported],['Anonymous sessions',data.anonymous_sessions],['Active · 15 min',data.active_sessions_15m],['Most attempts · one session',data.max_attempts_by_one_session],['P95 latency',data.p95_latency_ms===null?'—':`${data.p95_latency_ms} ms`]];
$('cards').replaceChildren(...cards.map(([label,value])=>{const el=document.createElement('article');el.className='card';const strong=document.createElement('strong'),span=document.createElement('span');strong.textContent=show(value);span.textContent=label;el.append(strong,span);return el}));
$('errors').replaceChildren();for(const [key,value] of Object.entries(data.errors||{})){const el=document.createElement('span');el.className='tag';el.textContent=`${key}: ${value}`;$('errors').append(el)}if(!$('errors').children.length)$('errors').textContent='No failures in this window.';
$('feedback').replaceChildren(pairs(data.feedback.ratings),pairs(data.feedback.categories));
$('hourly').replaceChildren(...data.hourly.map(row=>{const tr=document.createElement('tr');for(const value of [row.hour,row.translations,row.provider_attempts,row.errors]){const td=document.createElement('td');td.textContent=value;tr.append(td)}return tr}));
$('versions').replaceChildren(...data.versions.map(value=>{const li=document.createElement('li');li.textContent=value;return li}));$('state').textContent=`Updated for the last ${data.window_hours} hour(s).`;
}catch(error){$('state').textContent=error.message}}
$('window').addEventListener('change',load);load();

