const fs = require('fs'), path = require('path'), assert = require('assert');
const source = fs.readFileSync(path.join(__dirname, '../experiments/codex_translation_overlay.js'), 'utf8');
const extract = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
function element() {
  return {style: {}, children: [], setAttribute() {}, append(...nodes) {this.children.push(...nodes);},
    appendChild(node) {this.children.push(node);}, remove() {}, focus() {}};
}
async function verifyEnterFlow() {
  const handlers = new Set(), jobs = [], sent = [], notices = [];
  let now = 0, resolveRequest;
  const doc = {createElement: element, addEventListener(_name, callback) {handlers.add(callback);}, removeEventListener(_name, callback) {handlers.delete(callback);}};
  const root = element();root.contains = node => node !== editor;
  const editor = {isConnected: true, innerText: '请检查发送逻辑', focus() {}, contains: () => false};
  const state = {}, translateButton = {disabled: false}, directionSelect = {value: 'zh-en'};
  const api = new Function('document','root','state','controlsDark','findEditor','editorText','setEditorText','translateButton','directionSelect','request','enabledSendButton','showTranslationNotice','translationErrorMessage','Date','setTimeout',
    'const SEND_BUTTON_POLL_MS=1500,SEND_SETTLE_MS=250,accent="#5868df",config={translateInput:true};' +
    extract('function preview(original,translated,editor,direction)', "translateButton.addEventListener('pointerdown'") +
    extract('async function translateComposerInput()', '// click also covers') +
    extract('function textMatchesDirection(text,direction)', "document.addEventListener('keydown',inputTranslationShortcut,true)") +
    'return {translateComposerInput,inputTranslationShortcut};'
  )(doc,root,state,false,()=>editor,e=>e.innerText,(e,text)=>{e.innerText=text;return true;},translateButton,directionSelect,
    () => new Promise(resolve=>{resolveRequest=resolve;}),
    () => ({click(){sent.push(editor.innerText);editor.innerText='';}}),
    message=>notices.push(message), error=>error.message, {now:()=>now}, (callback,delay)=>jobs.push({callback,at:now+delay}));
  function key(target, extra = {}) {
    return {key:'Enter',target,prevented:false,preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;},...extra};
  }
  const first = key(editor);api.inputTranslationShortcut(first);
  assert(first.prevented);assert(translateButton.disabled);
  assert.strictEqual(editor.innerText, '请检查发送逻辑', 'first Enter must leave the original draft untouched');
  const early = key(editor);api.inputTranslationShortcut(early);
  assert(early.prevented, 'Enter during translation must not send the original');
  resolveRequest(['Please check sending behavior.']);await Promise.resolve();await Promise.resolve();
  const card = state.inputPreview.element.children[0], output = card.children[2];
  assert.strictEqual(output.value, 'Please check sending behavior.');
  for (const extra of [{shiftKey:true},{isComposing:true},{keyCode:229}]) {
    const event = key(output,extra);for(const handler of [...handlers])handler(event);
    assert(!event.prevented, 'newline and IME confirmation must not submit');
  }
  const repeated = key(output,{repeat:true});for(const handler of [...handlers])handler(repeated);
  assert(repeated.prevented);assert(state.inputPreview);
  output.value='Edited translated message';
  const second = key(output);for(const handler of [...handlers])handler(second);
  assert(second.prevented);assert.strictEqual(state.inputPreview,null);
  assert.strictEqual(editor.innerText,'Edited translated message');
  const duringSend = key(editor);api.inputTranslationShortcut(duringSend);
  assert(duringSend.prevented, 'Enter during send settlement must not duplicate submission');
  while(jobs.length){const job=jobs.shift();now=job.at;job.callback();}
  assert.deepStrictEqual(sent,['Edited translated message'], 'send must use the confirmed edited translation exactly once');
  assert.strictEqual(state.inputSendPending,false);
  assert.strictEqual(notices.length,0);
  editor.innerText='请检查空译文';
  const emptyRequest = api.translateComposerInput();resolveRequest(['']);await emptyRequest;
  assert.strictEqual(state.inputPreview,null);assert.strictEqual(editor.innerText,'请检查空译文');
  assert(notices[0].includes('空译文'), 'empty response must fail visibly, never fall back to source text');
  console.log('First Enter translation / second Enter confirmed send regressions OK');
}
verifyEnterFlow().catch(error=>{console.error(error);process.exitCode=1;});
