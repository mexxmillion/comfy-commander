const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../backend/app/static/generator.js'), 'utf8');
const helper = source.slice(source.indexOf('function outputActions('), source.indexOf('function renderGallery('));
function element(tag) {
  return {tagName: tag.toUpperCase(), children: [], style: {}, handlers: {},
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
