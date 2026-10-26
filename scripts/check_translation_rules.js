// Run the real overlay rules against isolated DOM fixtures; never touch live Codex.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../experiments/codex_translation_overlay.js'), 'utf8');
const section = (start, end) => {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert(a >= 0 && b > a, `missing section: ${start}`);
  return source.slice(a, b);
};
const state = {replacements: new Map(), rectCache: new Map(), cache: new Map()};
const config = {translateInterface: true, translateMessages: false};
let nodes = [];
const document = {body: {}, createTreeWalker() {
  let next = 0;
  return {currentNode: null, nextNode() {this.currentNode = nodes[next++]; return !!this.currentNode;}};
}};
const rules = new Function('config', 'state', 'root', 'document', 'NodeFilter', 'nodeRect', 'inViewport',
  section('const appName=', 'const dictionary =') +
  section('const dictionary =', 'const BODY_TRANSLATION_CONCURRENCY') +
  section('function baseExcluded(node)', 'function makeTranslatedRaw') +
  section('function replacementStillAllowed(node,entry)', 'async function translateMenuQueue') +
  section('function currentPageSnapshot()', 'function manualTranslationBatches') +
  'return {localTranslation,interfaceEligible,baseExcluded,currentPageSnapshot,replacementStillAllowed};'
)(config, state, {contains: () => false}, document, {SHOW_TEXT: 4}, () => ({}), () => true);
function fixture(text, {control = null, menu = false, message = false, anchor = false, composer = false} = {}) {
  return {nodeValue: text, parentElement: {closest(selector) {
    if (selector === 'a') return anchor ? {} : null;
    if (selector.includes('assistant-message')) return message ? {} : null;
    if (selector === '[role="menu"]') return menu ? {} : null;
    if (selector.includes('ComposerLayout')) return composer ? {} : null;
    if (selector.startsWith('button,')) return control;
    return null;
  }}};
}
for (const text of ['GPT-6.1', 'sol', 'Medium']) {
  const node = fixture(text, {control: {textContent: 'GPT-6.1 sol Medium'}});
  assert.strictEqual(rules.interfaceEligible(node), false, `model span ${text} must stay original`);
  assert.strictEqual(rules.localTranslation(text, node), null);
  assert.strictEqual(rules.replacementStillAllowed(node, {kind: 'interface', original: text}), false);
}
assert.strictEqual(rules.interfaceEligible(fixture('Model')), false);
assert.strictEqual(rules.interfaceEligible(fixture('Alt+Win+P')), false);
assert.strictEqual(rules.interfaceEligible(fixture('Anderson Jack', {menu: true})), false);
assert.strictEqual(rules.localTranslation('Workspace settings', fixture('Workspace settings')), '工作区设置');
assert.strictEqual(rules.localTranslation('Usage', fixture('Usage')), '用量');
assert.strictEqual(rules.localTranslation('65% left', fixture('65% left')), '剩余 65%');
assert.strictEqual(rules.localTranslation('Invite a coworker', fixture('Invite a coworker')), '邀请同事');
assert.strictEqual(rules.localTranslation('Show Mini', fixture('Show Mini')), '显示 Mini');
const link = fixture('Log out', {menu: true, anchor: true, control: {textContent: 'Log out'}});
assert.strictEqual(rules.interfaceEligible(link), true, 'menu links must be translatable');
assert.strictEqual(rules.localTranslation('Log out', link), '退出登录');
assert.strictEqual(rules.baseExcluded(fixture('Example link', {message: true, anchor: true})), true);
nodes = [link, fixture('sol', {control: {textContent: 'GPT-6.1 sol'}}), fixture('Anderson Jack', {menu: true})];
assert.deepStrictEqual(rules.currentPageSnapshot().map(item => item.original), ['Log out']);
config.translateInterface = false;
state.cache.set('zh:Log out', '退出登录');
assert.strictEqual(rules.interfaceEligible(link), false);
assert.deepStrictEqual(rules.currentPageSnapshot(), [], 'disabled interface must never be sent to AI');
assert.strictEqual(rules.replacementStillAllowed(link, {kind: 'interface', original: 'Log out'}), false);
async function checkDynamicMenu() {
  const queueState = {showTranslations: true, interfaceQueue: new Set(['Manage workspace']),
    interfacePending: new Set(), interfaceFailures: new Map(), interfaceRunning: false};
  let calls = 0, finish;
  const queue = new Function('state', 'request', 'cacheSet', 'scheduleRender', 'FAILURE_RECORD_LIMIT',
    section('async function translateMenuQueue()', 'function render()') +
    section('function manualTranslationBatches(texts)', 'async function translateCurrentPage()') +
    'const BATCH_MAX_ITEMS=8,BATCH_MAX_CHARACTERS=800; return translateMenuQueue;'
  )(queueState, (texts, target, scope) => {
    calls++;
    assert.deepStrictEqual(texts, ['Manage workspace']);
    assert.strictEqual(target, 'zh-CN');
    assert.strictEqual(scope, 'interface');
    return new Promise(resolve => {finish = resolve;});
  }, (key, value) => state.cache.set(key, value), () => {}, 600);
  const pending = queue();
  assert(queueState.interfacePending.has('Manage workspace'));
  await queue();
  assert.strictEqual(calls, 1, 'a pending menu request must not be duplicated');
  finish(['管理工作区']);
  await pending;
  assert.strictEqual(state.cache.get('zh:Manage workspace'), '管理工作区');
  assert.strictEqual(queueState.interfacePending.size, 0);
  assert.strictEqual(queueState.interfaceRunning, false);
  queueState.showTranslations = false;
  queueState.interfaceQueue.add('Manage workspace');
  await queue();
  assert.strictEqual(calls, 1, 'original mode must not start menu requests');
  queueState.showTranslations = true;
  const empty = queue();
  finish(['']);
  await empty;
  assert(queueState.interfaceFailures.has('Manage workspace'), 'empty results must enter retry backoff');
  console.log('Translation model, menu, link, scope and dynamic request regressions OK');
}
checkDynamicMenu().catch(error => {console.error(error); process.exitCode = 1;});
