// Exercise the real browser script against a minimal DOM and streamed fetch fixture.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function setup(events, fail=false, uploaded=null, confirm=true) {
  const nodes = new Map(), messages = [], exports = [], uploads = [];
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, {
      value: id === 'direction' ? 'en-ur' : '', disabled:false, hidden:true,
      listeners:{}, classList:{toggle(){}}, addEventListener(name, fn){this.listeners[name]=fn;},
      replaceChildren(){this.children=[];}, append(...items){this.children=(this.children||[]).concat(items);}, focus(){},click(){this.listeners.click?.();},
      set textContent(value){this.text=value; if(id==='message') messages.push(value);},
      get textContent(){return this.text;}
    });
    return nodes.get(id);
  };
  const context = {document:{getElementById:get,createElement:()=>({click(){},remove(){},append(...items){this.children=items;}}),body:{append(){}}}, window:{confirm:()=>confirm},
    URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},
    navigator:{clipboard:{writeText:async()=>{}}}, TextDecoder, AbortController, setTimeout, clearTimeout,
    fetch:async (url, options) => {
      if(url==='/api/status') return {ok:true,json:async()=>({configured:true})};
      if(url==='/api/upload') { uploads.push(options);return {ok:!fail,json:async()=>uploaded || {detail:'Unsupported document'}}; }
      if(url==='/api/export/docx') {exports.push(JSON.parse(options.body));return {ok:true,blob:async()=>({})};}
      if(fail) throw new Error('network unavailable');
      const bytes = new TextEncoder().encode(events.map(x=>JSON.stringify(x)).join('\n')+'\n');
      let at=0;
      return {ok:true,body:{getReader:()=>({
        async read(){if(at>=bytes.length)return {done:true};const value=bytes.slice(at,at+7);at+=7;return {done:false,value};},
        async cancel(){}
      })}};
    }};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('static/app.js','utf8'),context);
  return {get,messages,exports,uploads};
}

test('fragmented Urdu response, retry progress, and stale result protection',async()=>{
  const {get,messages}=setup([
    {type:'progress',state:'retrying',attempt:2,max_attempts:3,wait_seconds:2},
    {type:'result',translation:'درخواست منظور نہیں ہوئی۔',warnings:[],notes:[]}]);
  await new Promise(resolve=>setImmediate(resolve));
  get('source').value='Not approved'; get('source').listeners.input();
  await get('translator').listeners.submit({preventDefault(){}});
  assert.equal(get('output').value,'درخواست منظور نہیں ہوئی۔');
  assert.ok(messages.some(text=>text.includes('attempt 2 of 3')));
  assert.equal(get('copy').disabled,false);
  get('source').value='Changed source'; get('source').listeners.input();
  assert.equal(get('copy').disabled,true);
});

test('upload replaces text only on success and revokes approval',async()=>{
  const {get}=setup([],false,{text:'اردو درخواست',characters:12,warnings:['Check reading order.']});
  await new Promise(resolve=>setImmediate(resolve));
  get('source').value='Old';get('output').value='Old output';get('approve').checked=true;
  get('document').files=[{name:'office.docx',size:100}];
  await get('document').listeners.change();
  assert.equal(get('source').value,'اردو درخواست');assert.equal(get('output').value,'');
  assert.equal(get('approve').checked,false);assert.equal(get('download').disabled,true);
  assert.ok(get('upload-state').textContent.includes('Check reading order.'));
  assert.equal(get('translate').disabled,true);
  get('source-reviewed').checked=true;get('source-reviewed').listeners.change();
  assert.equal(get('translate').disabled,false);
  get('source').value+=' corrected';get('source').listeners.input();
  assert.equal(get('translate').disabled,true);
});

test('failed upload keeps current source and edited translation',async()=>{
  const {get,messages}=setup([],true);
  await new Promise(resolve=>setImmediate(resolve));
  get('source').value='Keep source';get('output').value='Keep edits';
  get('document').files=[{name:'broken.docx',size:100}];
  await get('document').listeners.change();
  assert.equal(get('source').value,'Keep source');assert.equal(get('output').value,'Keep edits');
  assert.equal(get('source').readOnly,false);assert.ok(messages.includes('Unsupported document'));
});

test('PDF uses shared attachment endpoint and page preview; removal clears approval and text',async()=>{
  const {get,uploads}=setup([],false,{text:'First page\n\nSecond page',characters:23,warnings:['Check order'],pages:[{page:1,start:0,end:10},{page:2,start:12,end:23}]});
  await new Promise(resolve=>setImmediate(resolve));
  get('document').files=[{name:'OFFICE.PDF',size:100}];
  await get('document').listeners.change();
  assert.equal(uploads.length,1);assert.equal(uploads[0].headers['Content-Type'],'application/pdf');
  assert.equal(get('page-preview').children.length,2);assert.equal(get('page-preview').hidden,false);
  assert.equal(get('attachment-name').textContent,'OFFICE.PDF');
  get('remove-attachment').listeners.click();
  assert.equal(get('source').value,'');assert.equal(get('output').value,'');
  assert.equal(get('download').disabled,true);assert.equal(get('page-preview').hidden,true);
});

test('invalid, empty and oversized files never upload',async()=>{
  const {get,uploads}=setup([]);
  await new Promise(resolve=>setImmediate(resolve));
  for(const file of [{name:'scan.png',size:100},{name:'old.doc',size:100},{name:'empty.pdf',size:0},{name:'large.pdf',size:2097153}]){
    get('document').files=[file];await get('document').listeners.change();
  }
  assert.equal(uploads.length,0);
});

test('cancelled replacement preserves existing work without uploading',async()=>{
  const {get,uploads}=setup([],false,null,false);
  await new Promise(resolve=>setImmediate(resolve));
  get('source').value='Keep this';get('document').files=[{name:'new.pdf',size:100}];
  await get('document').listeners.change();
  assert.equal(uploads.length,0);assert.equal(get('source').value,'Keep this');
});

test('approval gates export and exports exact edited text; edits and direction revoke approval',async()=>{
  const {get,exports}=setup([{type:'result',translation:'اصل مسودہ',warnings:[],notes:[]}]);
  await new Promise(resolve=>setImmediate(resolve));
  get('source').value='Original';get('source').listeners.input();
  await get('translator').listeners.submit({preventDefault(){}});
  await get('download').listeners.click();assert.equal(exports.length,0);
  get('output').value='Reviewed CNIC: [XXXXX-XXXXXXX-X]';get('output').listeners.input();
  get('approve').checked=true;get('approve').listeners.change();
  assert.equal(get('download').disabled,false);
  await get('download').listeners.click();
  assert.deepEqual(exports[0],{text:'Reviewed CNIC: [XXXXX-XXXXXXX-X]',direction:'en-ur',approved:true});
  get('output').value+=' changed';get('output').listeners.input();
  assert.equal(get('approve').checked,false);assert.equal(get('download').disabled,true);
  get('approve').checked=true;get('approve').listeners.change();
  get('source').value='Different';get('source').listeners.input();
  assert.equal(get('download').disabled,true);assert.equal(get('approve').disabled,true);
  get('direction').value='ur-en';get('direction').listeners.change();
  assert.equal(get('output').value,'');assert.equal(get('approve').checked,false);
});

test('failure retains source and prior edited output and releases controls',async()=>{
  const {get,messages}=setup([{type:'error',detail:'Provider quota exhausted'}]);
  await new Promise(resolve=>setImmediate(resolve));
  get('source').value='Keep source';get('source').listeners.input();get('output').value='My edits';
  await get('translator').listeners.submit({preventDefault(){}});
  assert.equal(get('source').value,'Keep source');assert.equal(get('output').value,'My edits');
  assert.equal(get('source').readOnly,false);
  assert.ok(messages.includes('Provider quota exhausted'));
});

test('incomplete stream cannot be mistaken for a completed translation',async()=>{
  const {get,messages}=setup([{type:'progress',state:'translating',attempt:1,max_attempts:3}]);
  await new Promise(resolve=>setImmediate(resolve));
  get('source').value='Keep source';get('source').listeners.input();
  await get('translator').listeners.submit({preventDefault(){}});
  assert.equal(get('output').value,'');
  assert.ok(messages.some(text=>text.includes('ended before translation completed')));
});
