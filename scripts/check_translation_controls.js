const fs = require('fs'), path = require('path'), assert = require('assert');
const source = fs.readFileSync(path.join(__dirname, '../experiments/codex_translation_overlay.js'), 'utf8');
const extract = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const style = {};
const controls = {style, getBoundingClientRect: () => ({width: 130, height: 88})};
let position = {x: 1, y: 1}, drag = null;
const positioning = new Function('inputControls', 'innerWidth', 'innerHeight', 'config', 'getPosition', 'getDrag',
  extract('function controlBounds(', 'function rememberControlPosition()') +
  extract('function parkControls()', 'const composerResizeObserver=')
    .replaceAll('if(controlDrag)', 'if(getDrag())').replaceAll('if(controlPosition)', 'if(getPosition())')
    .replaceAll('controlPosition.x', 'getPosition().x').replaceAll('controlPosition.y', 'getPosition().y') +
  'return {placeControls,positionInputButton};'
)(controls, 320, 200, {translateInput: true}, () => position, () => drag);
positioning.placeControls(900, -30);
assert.strictEqual(style.left, '182px');assert.strictEqual(style.top, '8px');
positioning.positionInputButton(null);
assert.strictEqual(style.left, '182px');assert.strictEqual(style.top, '104px');
drag = {};style.left = '30px';positioning.positionInputButton(null);
assert.strictEqual(style.left, '30px', 'periodic positioning must not interfere with dragging');

// Exercise the real preview actions in a small DOM fixture.
function element() {
  return {style: {}, children: [], setAttribute() {}, append(...nodes) {this.children.push(...nodes);},
    appendChild(node) {this.children.push(node);}, remove() {}, focus() {}};
}
const document = {createElement: element, addEventListener() {}, removeEventListener() {}};
const root = element(), state = {};
const editor = {isConnected: true, innerText: 'Original message', focus() {}};
const preview = new Function('document', 'root', 'state', 'controlsDark', 'editorText', 'setEditorText',
  'const accent="#5868df";' + extract('function preview(original,translated,editor,direction)', "translateButton.addEventListener('pointerdown'") + 'return preview;'
)(document, root, state, false, editor => editor.innerText, (editor, text) => {editor.innerText = text;return true;});
preview('Original message', '这是译文', editor, 'en-zh');
const card = root.children[0].children[0], actions = card.children[4];
actions.children.find(button => button.textContent === '仅写入').onclick();
assert.strictEqual(editor.innerText, '这是译文');assert.strictEqual(state.enterBypassText, '这是译文');
let intercepted = false;
let sendClicks = 0;
const shortcut = new Function('config', 'root', 'state', 'findEditor', 'translateButton', 'editorText', 'directionSelect', 'textMatchesDirection', 'enabledSendButton',
  extract('function inputTranslationShortcut(event)', "document.addEventListener('keydown',inputTranslationShortcut,true)") + 'return inputTranslationShortcut;'
)({translateInput: true}, {contains: () => false}, state, () => editor, {disabled: false}, editor => editor.innerText, {value: 'zh-en'}, () => true, () => ({click(){sendClicks++;}}));
shortcut({key: 'Enter', target: editor, preventDefault() {intercepted = true;},stopImmediatePropagation(){}});
assert.strictEqual(intercepted, true, 'approved translated drafts must use the native send button regardless of Enter preferences');
assert.strictEqual(sendClicks, 1);
const ownPreview = {isConnected: true, matches: () => false, closest: () => null, getBoundingClientRect: () => ({width: 600, height: 200, bottom: 700})};
const nativeEditor = {isConnected: true, matches: () => true, closest: () => null, getBoundingClientRect: () => ({width: 500, height: 18, bottom: 600})};
const questionReply = {isConnected: true, matches: () => false, closest: () => null, getBoundingClientRect: () => ({width: 650, height: 80, bottom: 780})};
const discover = new Function('state', 'root', 'document', 'innerHeight',
  'const config={};' + extract('function editorLooksLikeComposer(editor)', 'function editorText(editor)') + 'return findEditor;'
)({editor: ownPreview}, {contains: node => node === ownPreview}, {querySelectorAll: () => [ownPreview, questionReply, nativeEditor]}, 800);
assert.strictEqual(discover(), nativeEditor, 'the overlay preview must never be mistaken for Codex composer');
const makeButton = (label,disabled=false) => ({title:'',textContent:'',type:'button',disabled,getAttribute:name=>name==='aria-label'?label:null,getClientRects:()=>[{}]});
const stop=makeButton('停止'), send=makeButton('发送'), hidden=makeButton('Send');hidden.getClientRects=()=>[];
const inputScope={querySelectorAll:()=>[]}, footer={querySelectorAll:()=>[hidden,stop,send]};
const nestedEditor={closest:selector=>selector==='form'?null:selector.includes('ComposerLayoutRoot')?footer:inputScope};
const buttons = new Function('root','const config={};'+extract('function sendButtonKind(button)', 'function findSendButton(editor)')+
  extract('function fallbackSendButton(editor)', 'function preview(original,translated,editor,direction)')+'return {composerButtons,enabledSendButton};')({contains:()=>false});
assert.strictEqual(buttons.enabledSendButton(nestedEditor),send,'nested ComposerLayoutInput must not hide sibling footer buttons');
send.disabled=true;assert.strictEqual(buttons.enabledSendButton(nestedEditor),null,'disabled Send and Stop must never be clicked');
console.log('Movable controls, nested composer and approved-draft send regressions OK');
