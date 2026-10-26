const assert = require('assert');
const fs = require('fs');
const path = require('path');
const ui = fs.readFileSync(path.resolve(__dirname, '..', 'ui.html'), 'utf8');
const engine = fs.readFileSync(path.resolve(__dirname, '..', 'experiments/codex_translation_overlay.js'), 'utf8');

module.exports = async function verifyClaudePageIntegration() {
  // Exercise the production start handler, including duplicate-start suppression.
  const elements = {
    'translation-apply': {disabled: false},
    'claude-translation-status': {textContent: ''},
    'claude-enter': {checked: false},
  };
  const options = {provider_id:'service', model:'translation-model',
    translate_interface:false, translate_messages:true, translate_input:true};
  const calls = [];
  let finish;
  const api = (name, data) => {
    calls.push({name, data});
    return new Promise(resolve => {finish = resolve;});
  };
  const start = ui.indexOf('async function openClaudeTranslation()');
  const end = ui.indexOf('async function closeClaudeTranslation()', start);
  const open = new Function('document', 'api', 'readTranslationForm', 'selectedToolboxApp', 'claudeChannelCurrent',
    ui.slice(start, end) + 'return openClaudeTranslation;')(
      {getElementById:id=>elements[id]}, api, ()=>({...options}), 'claude', 'claude-official');
  const pending = open();
  await open();
  assert.strictEqual(calls.length, 1, 'a pending attachment must not be duplicated');
  assert.strictEqual(calls[0].name, 'open_claude_translation');
  assert.deepStrictEqual(calls[0].data, {...options, intercept_enter:false});
  finish({ok:true, message:'Attached'});
  await pending;
  assert.strictEqual(elements['translation-apply'].disabled, false);
  assert.strictEqual(elements['claude-translation-status'].textContent, 'Attached');

  const stopStart = ui.indexOf('async function closeClaudeTranslation()');
  const stopEnd = ui.indexOf('function applySelectedTranslation()', stopStart);
  const stopWith = result => new Function('document', 'api', 'toast',
    ui.slice(stopStart, stopEnd) + 'return closeClaudeTranslation;')(
      {getElementById:id=>elements[id]}, async()=>result, ()=>{});
  await stopWith({ok:false,error:'Stop failed'})();
  assert.strictEqual(elements['claude-translation-status'].textContent,'Stop failed');
  await stopWith(null)();
  assert(elements['claude-translation-status'].textContent.startsWith('无法确认'));
  await stopWith({ok:true})();
  assert(elements['claude-translation-status'].textContent.includes('已关闭'));

  // Use the shared send resolver with the actual Claude composer scope and label.
  const section = (a,b)=>engine.slice(engine.indexOf(a),engine.indexOf(b,engine.indexOf(a)));
  const send = {disabled:false,type:'button',title:'',textContent:'',
    getAttribute:name=>name==='aria-label'?'发送消息':null,getClientRects:()=>[{}]};
  const model = {...send,getAttribute:()=> '模型：Sonnet 5.5 中等'};
  const scope = {querySelectorAll:()=>[model,send]};
  const editor = {closest:selector=>selector==='.rounded-composer'?scope:null};
  const sender = new Function('config','root',
    section('function sendButtonKind(button)', 'function findSendButton(editor)')+
    section('function fallbackSendButton(editor)', 'function preview(original,translated,editor,direction)')+
    'return enabledSendButton;')({composerScopeSelector:'.rounded-composer'}, {contains:()=>false});
  assert.strictEqual(sender(editor), send);
  send.disabled=true;
  assert.strictEqual(sender(editor), null);
  console.log('Claude page launch, shared settings and native send regressions OK');
};
