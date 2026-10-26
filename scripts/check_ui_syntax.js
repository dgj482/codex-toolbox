const fs = require('fs');
const path = require('path');
const assert = require('assert');

const uiPath = path.resolve(__dirname, '..', 'ui.html');
const html = fs.readFileSync(uiPath, 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
if (!scripts.length) throw new Error('ui.html contains no inline script');
for (const [, source] of scripts) new Function(source);
const launchButtons = html.match(/onclick="launchCodexHub\(\)"/g) || [];
if (launchButtons.length !== 1) throw new Error(`expected one central launch button, found ${launchButtons.length}`);
for (const feature of ['channel_switching', 'session_routing', 'session_archive', 'local_appearance', 'surface_translation']) {
  if (!html.includes(`data-feature-toggle="${feature}"`)) throw new Error(`missing feature toggle: ${feature}`);
}
for (const stale of ['launchCodexWithSavedBackground', 'startLiveBackgroundPreview', 'applySurfaceTranslation(true)', 'function applyBackground(']) {
  if (html.includes(stale)) throw new Error(`stale launch path remains: ${stale}`);
}
const overlay = fs.readFileSync(path.resolve(__dirname, '..', 'experiments', 'codex_translation_overlay.js'), 'utf8');
new Function(overlay.replace('__CODEX_TOOLBOX_CONFIG__', '{}'));
const matcherStart = overlay.indexOf('function textMatchesDirection(text,direction)');
const matcherEnd = overlay.indexOf('function inputTranslationShortcut(event)', matcherStart);
if (matcherStart < 0 || matcherEnd < 0) throw new Error('composer direction logic missing');
const textMatchesDirection = new Function(overlay.slice(matcherStart, matcherEnd) + 'return textMatchesDirection;')();
if (!textMatchesDirection('请检查 D:\\project\\scripts\\translation_overlay.js 是否能翻译', 'zh-en')) {
  throw new Error('Chinese prompts with Latin paths must still translate');
}
if (textMatchesDirection('Please inspect src/file.py', 'zh-en')) {
  throw new Error('English-only prompts should not be intercepted');
}
if (!overlay.includes("translateButton.addEventListener('click'") || overlay.includes("translateButton.addEventListener('pointerup'")) {
  throw new Error('Translate button must support mouse and keyboard activation once');
}
const editorStart = overlay.indexOf('function editorText(editor)');
const editorEnd = overlay.indexOf('function editorIsUncovered(editor)', editorStart);
assert(editorStart >= 0 && editorEnd > editorStart, 'composer write logic missing');
const editorApi = new Function('HTMLTextAreaElement', 'HTMLInputElement', 'document', 'getSelection',
  overlay.slice(editorStart, editorEnd) + 'return {editorText,setEditorText};');
const selection = {removeAllRanges() {}, addRange() {}};
const editor = {innerText: '修复了什么功能', focus() {}};
const documentStub = {createRange: () => ({selectNodeContents(node) {assert.strictEqual(node, editor);}}), execCommand() {return false;}};
let editing = editorApi(class Textarea {}, class Input {}, documentStub, () => selection);
assert.strictEqual(editing.setEditorText(editor, 'What features were fixed?'), false);
assert.strictEqual(editor.innerText, '修复了什么功能', 'failed native edit must not replace DOM text directly');
documentStub.execCommand = (command, _showUi, value) => {assert.strictEqual(command, 'insertText'); editor.innerText = value; return true;};
editing = editorApi(class Textarea {}, class Input {}, documentStub, () => selection);
assert.strictEqual(editing.setEditorText(editor, 'What features were fixed?'), true);
assert.strictEqual(editor.innerText, 'What features were fixed?');
const buttonsStart = overlay.indexOf('function sendButtonKind(button)');
const buttonsMiddle = overlay.indexOf('function parkControls()', buttonsStart);
const fallbackStart = overlay.indexOf('function fallbackSendButton(editor)', buttonsMiddle);
const buttonsEnd = overlay.indexOf('function preview(original,translated,editor,direction)', buttonsStart);
assert(buttonsStart >= 0 && buttonsMiddle > buttonsStart && fallbackStart > buttonsMiddle && buttonsEnd > fallbackStart, 'composer send logic missing');
const sendApi = new Function('root', 'const config={};'+overlay.slice(buttonsStart, buttonsMiddle) + overlay.slice(fallbackStart, buttonsEnd) + 'return {enabledSendButton};')({contains: () => false});
const stop = {disabled: false, type: 'button', getAttribute: () => 'Stop', title: '', textContent: 'Stop'};
const send = {disabled: false, type: 'button', getAttribute: () => 'Send', title: '', textContent: 'Send'};
const footer = {querySelectorAll: () => [stop]};
const scope = {querySelectorAll: () => [stop]};
const composer = {closest: selector => selector.includes('Footer') ? footer : scope};
assert.strictEqual(sendApi.enabledSendButton(composer), null, 'Stop must never be clicked as Send');
footer.querySelectorAll = () => [send];
scope.querySelectorAll = () => [send];
assert.strictEqual(sendApi.enabledSendButton(composer), send);
const labelledButton = (label, extra = {}) => ({disabled: false, type: 'button', title: '', textContent: '', getAttribute: key => key === 'aria-label' ? label : null, ...extra});
for (const label of ['Send message', 'Send (Enter)', '发送消息', 'Queue message', 'Steer']) {
  const action = labelledButton(label);scope.querySelectorAll = () => [action];
  assert.strictEqual(sendApi.enabledSendButton(composer), action);
}
for (const action of [labelledButton('Stop generating', {type: 'submit'}), labelledButton('Cancel', {type: 'submit'}), labelledButton('Send', {getClientRects: () => []}), labelledButton('Send', {getAttribute: key => key === 'aria-disabled' ? 'true' : 'Send'})]) {
  scope.querySelectorAll = () => [action];assert.strictEqual(sendApi.enabledSendButton(composer), null);
}
assert(overlay.includes('Date.now()>=started+SEND_SETTLE_MS'), 'send must wait for editor settlement');
assert(overlay.includes('editorText(target).trim()!==translated.trim()'), 'send must verify translated draft');
require('./check_translation_rules.js');
require('./check_translation_controls.js');
require('./check_translation_enter.js');
require('./check_claude_translation.js')().catch(error => {console.error(error); process.exitCode = 1;});
console.log('UI and translation overlay contracts OK');
