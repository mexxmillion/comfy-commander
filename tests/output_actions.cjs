const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../backend/app/static/generator.js'), 'utf8');
const helper = source.slice(source.indexOf('function outputActions('), source.indexOf('function renderGallery('));
function element(tag) {
  return {tagName: tag.toUpperCase(), children: [], style: {}, handlers: {},
    setAttribute(name, value) { this[name] = value; },
    append(...items) { this.children.push(...items); },
    prepend(...items) { this.children.unshift(...items); },
    addEventListener(name, callback) { this.handlers[name] = callback; }};
}
const calls = [];
const context = {document: {createElement: element}, location: {},
  loadRecipe: recipe => calls.push(['edit', recipe]),
  requeueRecipe: recipe => calls.push(['requeue', recipe]),
  keepSetup: file => calls.push(['save', file]),
  removeGalleryItem: file => calls.push(['unsave', file]),
  viewGalleryOutput: file => calls.push(['view', file]),
  shareMedia: file => calls.push(['share', file]),
  isImageOutput: file => file.filename.endsWith('.png'),
  mediaUrl: file => `/media/${file.filename}`};
vm.createContext(context);
vm.runInContext(helper, context);
const file = {filename: 'result.png', job_id: 'job-1', task_id: 'task-1',
  recipe: {mode: 'qwen', seed: 42}};
const thumbnail = element('img');
const actions = context.outputActions(file, thumbnail);
assert.equal(calls.length, 0, 'Rendering actions must never submit or mutate');
assert.deepEqual(actions.children.map(item => item.textContent),
  ['View / play', 'Edit & rerun', 'Requeue same', 'Save preset', 'Save image', 'Download']);
actions.children[1].handlers.click();
assert.equal(context.location.hash, '#generator');
assert.equal(calls[0][1], file.recipe);
actions.children[2].handlers.click();
assert.equal(calls[1][0], 'requeue');
thumbnail.onclick();
assert.equal(calls[2][1], file);
assert.equal(actions.children.at(-1).download, file.filename);
assert.deepEqual(context.outputActions({filename: 'old.mp4'}).children.map(item => item.textContent),
  ['View / play', 'Save video', 'Download']);
const saved = context.outputActions({...file, saved: true}).children.map(item => item.textContent);
assert.ok(saved.includes('Rename preset') && saved.includes('Remove preset'));
assert.equal(context.outputActions({...file, status: 'Running'}).children.length, 0);
assert.equal(context.outputActions({...file, status: 'Queued'}).children.length, 0);
const slateStart = source.indexOf('function outputSlate(');
vm.runInContext(source.slice(slateStart, source.indexOf('function outputActions(', slateStart)), context);
const slate = context.outputSlate({status: 'Running', progress: 35});
assert.equal(slate.children[0].textContent, 'Rendering');
assert.equal(slate.children[1].textContent, '35%');
assert.equal(slate.role, 'status');
console.log('Shared gallery/output action behavior passed');

// Historical ACC recipes must not fall through to submission after rejection.
const loadStart = source.indexOf('function loadRecipe(recipe)');
const loadGuard = source.slice(loadStart, source.indexOf("  $('#ai-prompt-only')", loadStart)) + '\n}';
const requeueStart = source.indexOf('function requeueRecipe(recipe)');
const requeue = source.slice(requeueStart, source.indexOf('\nasync function keepSetup', requeueStart));
context.normalizeRecipe = recipe => recipe;
context.toast = message => calls.push(['toast', message]);
context.setTimeout = () => assert.fail('Rejected ACC recipe must not schedule submission');
vm.runInContext(loadGuard + '\n' + requeue, context);
context.requeueRecipe({mode: 'h3', turbo: 10});
assert.match(calls.at(-1)[1], /ACC presets are no longer supported/);
console.log('Historical ACC requeue rejection passed');

// Exercise the production model chain and native DynamicCombo serialization.
const chainStart = source.indexOf('  let previous = "127";', source.indexOf('function buildH3Workflow'));
const chainEnd = source.indexOf('  if (!frameMode && !singleFrame', chainStart);
const constants = source.slice(source.indexOf('const TURBO_LORAS ='), source.indexOf('const FRAME_H3_MODEL'));
const sparseHelpers = source.slice(source.indexOf('function sparseAttentionInputs('), source.indexOf('function buildH3Workflow('));
for (const steps of [4, 8]) {
  for (const enabled of [false, true]) {
    for (const method of ['sla', 'vsa', 'sol-attn']) {
      for (const omni of [false, true]) {
        const controls = {'#sparse-enabled': {checked: enabled}, '#sparse-method': {value: method}};
        const graphContext = {workflow: {'168': {inputs: {}}}, turboEnabled: !omni,
          turboSteps: steps, frameMode: false, omni, eros: false,
          selectedLoraEntries: () => [], $: selector => controls[selector]};
        vm.createContext(graphContext);
        vm.runInContext(constants + sparseHelpers + source.slice(chainStart, chainEnd), graphContext);
        assert.equal(Boolean(graphContext.workflow['145']), !omni);
        if (!omni) assert.match(graphContext.workflow['145'].inputs.lora_name, new RegExp(`ref2v_turbo_${steps}step`));
        const node = graphContext.workflow['146'];
        assert.equal(Boolean(node), enabled);
        if (enabled) {
          assert.equal(node.class_type, 'BlockSparseAttention');
          assert.equal(node.inputs.selection, method);
          assert.equal(node.inputs[method === 'sol-attn' ? 'selection.tau' : 'selection.keep_percent'], method === 'sol-attn' ? 1.3 : 10);
          assert.equal(node.inputs.sink_conditioning, 'exact_kv_and_rows');
          assert.equal(node.inputs.model[0], omni ? '127' : '145');
        }
        assert.equal(graphContext.workflow['168'].inputs.model[0], enabled ? '146' : omni ? '127' : '145');
      }
    }
  }
}
const controls = {'#h3-model': {value: 'omni'}, '#sparse-enabled': {}, '#sparse-method': {}};
const defaults = {state: {h3Type: 'reference'}, OMNI_MODEL: 'omni', $: selector => controls[selector]};
vm.createContext(defaults);vm.runInContext(sparseHelpers, defaults);
defaults.sparseAttentionDefaults();
assert.equal(controls['#sparse-enabled'].checked, true);
assert.equal(controls['#sparse-method'].value, 'vsa');
controls['#h3-model'].value = 'ref2v';defaults.sparseAttentionDefaults();
assert.equal(controls['#sparse-enabled'].checked, false);
controls['#sparse-enabled'].checked = true;controls['#sparse-method'].value = 'untrusted';
assert.throws(() => defaults.sparseAttentionInputs(['127', 0]), /Choose SLA, VSA or SOL/);
console.log('Sparse methods, model chains and Omni/Ref2V defaults passed');

const consoleSource = fs.readFileSync(path.join(__dirname, '../backend/app/static/console.js'), 'utf8');
const countsStart = consoleSource.indexOf('  function syncNavCounts(metrics)');
const countsEnd = consoleSource.indexOf('  function onJobSubmitted()', countsStart);
const badges = new Map();
const countContext = {document: {querySelectorAll(selector) {
  if (!badges.has(selector)) badges.set(selector, [{}]);
  return badges.get(selector);
}}};
vm.createContext(countContext);
vm.runInContext(consoleSource.slice(countsStart, countsEnd), countContext);
countContext.syncNavCounts({queued: 3, running: 2, active_workers: 4});
assert.equal(badges.get('[data-dispatch-queued]')[0].textContent, '3');
assert.equal(badges.get('[data-dispatch-running]')[0].textContent, '2');
countContext.syncNavCounts({queued: 0, running: 0});
assert.equal(badges.get('[data-dispatch-queued]')[0].textContent, '0');
assert.equal(badges.get('[data-dispatch-running]')[0].textContent, '0');
console.log('Live Dispatcher sidebar counts passed');
