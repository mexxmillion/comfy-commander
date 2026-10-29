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
  ['View image', 'Edit & rerun', 'Requeue same', 'Save preset', 'Save image', 'Download']);
actions.children[1].handlers.click();
assert.equal(context.location.hash, '#generator');
assert.equal(calls[0][1], file.recipe);
actions.children[2].handlers.click();
assert.equal(calls[1][0], 'requeue');
thumbnail.onclick();
assert.equal(calls[2][1], file);
assert.equal(actions.children.at(-1).download, file.filename);
assert.deepEqual(context.outputActions({filename: 'old.mp4'}).children.map(item => item.textContent),
  ['▶ Play video', 'Save video', 'Download']);
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

const viewerChildren = new Map([
  ['#gallery-viewer-media', {replaceChildren(media) { this.media = media; }}],
  ['#gallery-viewer-title', {}],
]);
let movedToBody = false;
const viewer = {querySelector: selector => viewerChildren.get(selector),
  showModal() { assert.equal(movedToBody, true, 'Outputs player must escape the hidden Generator'); }};
const viewerContext = {document: {createElement: element,
  querySelector: selector => { assert.equal(selector, '#gallery-viewer'); return viewer; },
  body: {append(node) { assert.equal(node, viewer); movedToBody = true; }}},
  mediaUrl: file => `/media/${file.filename}`};
vm.createContext(viewerContext);
const viewerStart = source.indexOf('function viewGalleryOutput(');
vm.runInContext(source.slice(viewerStart, source.indexOf('\nconst gallerySelection', viewerStart)), viewerContext);
viewerContext.viewGalleryOutput({filename: 'result.mp4'});
assert.equal(viewerChildren.get('#gallery-viewer-media').media.tagName, 'VIDEO');
assert.equal(viewerChildren.get('#gallery-viewer-media').media.src, '/media/result.mp4');
console.log('Outputs video player placement passed');

const processingControls = new Map();
for (const id of ['isolation-text', 'isolation-confidence', 'isolation-selection', 'isolation-crop',
  'isolation-background', 'isolation-find', 'isolation-enabled', 'reference-depth',
  'reference-depth-invert', 'reference-cache', 'isolation-apply', 'reference-isolation']) {
  processingControls.set('#' + id, {value: '', checked: false});
}
processingControls.get('#isolation-text').value = 'person';
processingControls.get('#isolation-confidence').value = '0.25';
processingControls.get('#isolation-enabled').checked = true;
let editorClosed = false;
processingControls.get('#reference-isolation').close = () => { editorClosed = true; };
const reference = {kind: 'image', file: 'original.png'};
const processingContext = {$: selector => processingControls.get(selector),
  isolationEditor: {target: reference, mode: 'h3', busy: false, result: null},
  state: {mode: 'h3', savedReferences: [reference]}, toast() {},
  markAiPromptStale() {}, renderReferences() {}, setStatus() {}};
vm.createContext(processingContext);
const busyStart = source.indexOf('function setIsolationBusy(');
vm.runInContext(source.slice(busyStart, source.indexOf('function showIsolationPreview(', busyStart)), processingContext);
processingContext.setIsolationBusy(false);
assert.equal(processingControls.get('#isolation-apply').disabled, true);
assert.equal(processingControls.get('#isolation-find').disabled, false);
const saveStart = source.indexOf('$("#isolation-apply").onclick = () => {');
const saveEnd = source.indexOf('\n};', saveStart) + 3;
vm.runInContext(source.slice(saveStart, saveEnd), processingContext);
processingControls.get('#isolation-apply').onclick();
assert.equal(processingContext.state.savedReferences[0], reference);
assert.equal(editorClosed, false);
processingContext.isolationEditor.result = {kind: 'image', file: 'processed.png', preprocess: {source: 'original.png'}};
processingContext.setIsolationBusy(false);
assert.equal(processingControls.get('#isolation-apply').disabled, false);
processingControls.get('#isolation-apply').onclick();
assert.equal(processingContext.state.savedReferences[0].file, 'processed.png');
assert.equal(processingContext.state.savedReferences[0].preprocess.source, 'original.png');
assert.equal(editorClosed, true);
console.log('Reference preview-before-save and reviewed-image reuse passed');

vm.runInContext(source.slice(source.indexOf('function isolationProgressText('), source.indexOf('async function previewIsolation()')), context);
assert.match(context.isolationProgressText({status: 'queued', wait_reason: 'no_worker'}), /Waiting for a compatible/);
assert.match(context.isolationProgressText({status: 'queued', wait_reason: 'queue', queue_position: 4}), /position 4/);
assert.match(context.isolationProgressText({status: 'queued', wait_reason: 'workers_paused'}), /paused/);
assert.match(context.isolationProgressText({status: 'running', progress: 25}), /Processing.*25%/);
console.log('Preview queue and worker waiting messages passed');

const buildStart = source.indexOf('function buildH3Workflow(');
const buildEnd = source.indexOf('\nfunction ', buildStart + 5);
for (const roles of [[], ['first_frame'], ['last_frame'], ['first_frame', 'last_frame']]) {
  const values = {'#frame-h3-model': 'minimax_h3_fl2va_pruned_int8_convrot.safetensors',
    '#h3-model': 'ref2v', '#total-steps': 5, '#megapixels': 0.5, '#duration': 5,
    '#aspect-ratio': '9:16', '#frame-turbo-strength': 0.5, '#direction': 'A cinematic camera move'};
  const buildContext = {state: {h3Type: 'frames'}, EROSMAX_MODEL: 'unsupported',
    SINGULARITY_MODEL: 'singularity', OMNI_MODEL: 'omni', H3_TEXT_ENCODER: 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',
    VSA_LORA: 'fasth3_vsa_gate.safetensors', selectedLoraEntries: () => [], sparseAttentionInputs: () => null,
    studioRoot: {querySelector: () => ({value: '4'})},
    $: selector => ({value: values[selector] ?? '', checked: selector === '#turbo-enabled'})};
  vm.createContext(buildContext);
  vm.runInContext(constants + source.slice(buildStart, buildEnd), buildContext);
  const graph = buildContext.buildH3Workflow(roles.map(kind => ({kind, file: `h3_mobile/${kind}.png`})), 42);
  assert.equal(graph['131'].class_type, 'MiniMaxH3ImageToVideo');
  assert.equal(graph['127'].inputs.unet_name, values['#frame-h3-model']);
  assert.match(graph['145'].inputs.lora_name, /fl2v_turbo_4step/);
  for (const kind of ['first_frame', 'last_frame']) assert.equal(Boolean(graph['131'].inputs[kind]), roles.includes(kind));
  assert.equal(Boolean(graph['146']), false);
}
console.log('FLF2V text-only, first, last and both-keyframe graph checks passed');
