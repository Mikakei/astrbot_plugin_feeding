// Run with: node --test tests/test_customize.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..', 'pages', 'customize');
const script = new vm.Script(fs.readFileSync(path.join(root, 'app.js'), 'utf8'));

async function boot({ missingBridge = false, failLoad = false } = {}) {
  const nodes = new Map();
  function element() {
    return { value: '', textContent: '', disabled: false, children: [],
      classList: { toggle() {} }, listeners: {},
      append(...items) { this.children.push(...items); for (const item of items) if (item.id) nodes.set(item.id, item); },
      replaceChildren(...items) { this.children=[]; this.append(...items); },
      showModal() { this.open=true; }, close() { this.open=false; },
      addEventListener(type, fn) { this.listeners[type] = fn; } };
  }
  const get = id => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); };
  // These controls are disabled in the initial HTML before JavaScript runs.
  get('save').disabled = true;
  get('fields').disabled = true;
  const result = { settings: { guest_roles: [], steal_mode:'average', steal_probability:15, steal_enabled:true }, defaults: {}, revision: 'rev-1' };
  let saved;
  const window = { addEventListener() {}, AstrBotPluginPage: missingBridge ? undefined : {
    ready: async () => ({ isDark: false }), onContext() {},
    apiGet: async route => { assert.equal(route, 'customization'); if (failLoad) throw Error('offline'); return result; },
    apiPost: async (route, data) => { assert.equal(route, 'customization/save'); saved = data; return { ...result, settings: data.settings }; }
  } };
  // Sandboxed pages cannot use localStorage; initialization must not touch it.
  Object.defineProperty(window, 'localStorage', { get() { throw Error('sandbox storage denied'); } });
  await script.runInNewContext({ window, document: {
    getElementById: get, createElement: element, querySelector: get, body: element()
  }, confirm: () => true });
  return { get, saved: () => saved };
}

test('entry uses a deferred classic script without CORS opt-in', () => {
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const entry = html.match(/<script\b[^>]*src="\.\/app\.js"[^>]*>/)[0];
  assert.match(entry, /\bdefer\b/);
  assert.doesNotMatch(entry, /type\s*=\s*["']module|crossorigin/i);
});

test('classic initialization loads configuration and saves through the bridge', async () => {
  const ui = await boot();
  assert.equal(ui.get('status').textContent, '已载入，修改后请保存。');
  assert.equal(ui.get('fields').disabled, false);
  assert.equal(ui.get('references').children.length, 2);
  assert.equal(ui.get('replies').children.length, 8);
  ui.get('review_prompt').value = 'test style';
  ui.get('editor').listeners.input({ target: { tagName: 'TEXTAREA' } });
  assert.equal(ui.get('save').disabled, false);
  await ui.get('editor').onsubmit({ preventDefault() {} });
  assert.equal(ui.saved().revision, 'rev-1');
  assert.equal(ui.saved().settings.review_prompt, 'test style');
  assert.equal(ui.get('status').textContent, '已保存，对后续投喂生效。');
});

test('role creation, mode conversion and over-allocation feedback', async () => {
  const ui=await boot();
  ui.get('add-guest').onclick();
  const card=ui.get('guest-list').children[0];
  assert.equal(card.open,true);
  await ui.get('editor').onsubmit({preventDefault(){}});
  const role=ui.saved().settings.guest_roles[0];
  assert.equal(role.name,'新角色');
  ui.get(role.id+'-persona').value='独立人格';ui.get(role.id+'-persona').oninput();
  // Reference upload is handled by the bridge; emulate an already uploaded file.
  role.reference=['files/guest_reference/test.png'];
  ui.get('steal_mode').value='custom';ui.get('steal_mode').onchange();
  assert.equal(role.probability,15);
  ui.get(role.id+'-probability').value='101';ui.get(role.id+'-probability').oninput();
  assert.match(ui.get('probability-error').textContent,/超过上限/);
  assert.equal(ui.get('save').disabled,true);
});

test('missing bridge and failed loading show errors and prevent saving', async () => {
  for (const options of [{ missingBridge: true }, { failLoad: true }]) {
    const ui = await boot(options);
    assert.match(ui.get('status').textContent, options.missingBridge ? /请从 AstrBot/ : /载入失败：offline/);
    assert.equal(ui.get('save').disabled, true);
  }
});

test('sandbox-compatible delete confirmation can cancel or accept', async () => {
  const ui=await boot();ui.get('add-guest').onclick();
  let card=ui.get('guest-list').children[0];
  let action=card.children[card.children.length-1].children[1];
  let pending=action.onclick();
  assert.equal(ui.get('confirm-dialog').open,true);
  ui.get('confirm-cancel').onclick();await pending;
  assert.equal(ui.get('guest-list').children.length,1);
  pending=action.onclick();ui.get('confirm-accept').onclick();await pending;
  assert.equal(ui.get('guest-list').children.length,0);
});
