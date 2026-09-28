// Decorative effects only: no real account, network, or browser required.
// Run: node --test 工具/tests/test_oauth_hero_motion.cjs
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const file = path.join(__dirname, '../../网页/oauth/oauth.js');
const fullSource = readFileSync(file, 'utf8');
const marker = '/* Decorative pointer response only. No account, button, navigation, or API behavior. */';
assert.equal(fullSource.split(marker).length, 2, 'one isolated decorative entry point');
const source = fullSource.slice(fullSource.indexOf(marker));

function harness(options = {}) {
  const properties = new Map();
  const frames = new Map();
  const listeners = { surface: new Map(), window: new Map(), document: new Map(), fine: new Map(), reduced: new Map() };
  const target = name => ({ addEventListener(type, callback, config) { listeners[name].set(type, { callback, config }); } });
  const surface = target('surface');
  const scene = {
    style: {
      setProperty(name, value) { properties.set(name, value); },
      removeProperty(name) { properties.delete(name); },
    },
    closest(selector) { assert.equal(selector, '.intro'); return options.noSurface ? null : surface; },
    getBoundingClientRect() { return options.bounds || { left: 100, top: 50, width: 1000, height: 600 }; },
  };
  const fine = { ...target('fine'), matches: options.fine ?? true };
  const reduced = { ...target('reduced'), matches: options.reduced ?? false };
  const document = {
    ...target('document'), hidden: options.hidden ?? false,
    getElementById(id) { assert.equal(id, 'hero-field'); return options.noScene ? null : scene; },
  };
  let sequence = 0;
  const window = {
    ...target('window'),
    matchMedia(query) {
      if (query === '(hover: hover) and (pointer: fine)') return fine;
      assert.equal(query, '(prefers-reduced-motion: reduce)');
      return reduced;
    },
    requestAnimationFrame(callback) { frames.set(++sequence, callback); return sequence; },
    cancelAnimationFrame(id) { frames.delete(id); },
  };
  if (options.noStyle) delete scene.style;
  if (options.noMedia) delete window.matchMedia;
  if (options.noAnimation) delete window.requestAnimationFrame;
  vm.runInNewContext(source, { document, window }, { filename: file });
  const emit = (scope, type, event = {}) => listeners[scope].get(type)?.callback(event);
  const move = (clientX = 600, clientY = 350, pointerType = 'mouse') => emit('surface', 'pointermove', { clientX, clientY, pointerType });
  const flush = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback()); };
  return { properties, frames, listeners, fine, reduced, document, emit, move, flush };
}

test('decorative layer tolerates missing markup and unsupported APIs', () => {
  for (const options of [{ noScene: true }, { noStyle: true }, { noSurface: true }, { noMedia: true }, { noAnimation: true }]) {
    const state = harness(options);
    assert.equal(state.listeners.surface.size, 0);
    assert.equal(state.frames.size, 0);
  }
});

test('listeners are scoped and never intercept buttons or clicks', () => {
  const state = harness();
  assert.deepEqual([...state.listeners.surface.keys()], ['pointermove', 'pointerleave', 'pointercancel']);
  for (const listener of state.listeners.surface.values()) assert.equal(listener.config.passive, true);
  assert.deepEqual([...state.listeners.window.keys()], ['pagehide']);
  assert.deepEqual([...state.listeners.document.keys()], ['visibilitychange']);
});

test('pointer bursts render at most once per animation frame using the latest position', () => {
  const state = harness();
  state.move(100, 50);
  state.move(300, 200);
  state.move(600, 350);
  assert.equal(state.frames.size, 1);
  assert.equal(state.properties.size, 0);
  state.flush();
  assert.equal(state.frames.size, 0);
  assert.deepEqual(Object.fromEntries(state.properties), {
    '--pointer-x': '50.00%', '--pointer-y': '50.00%', '--pointer-active': '1', '--drift-x': '0.00px', '--drift-y': '0.00px',
  });
});

test('glow position and parallax remain clamped to the decorative scene', () => {
  const state = harness();
  state.move(-5000, -5000);
  state.flush();
  assert.equal(state.properties.get('--pointer-x'), '0.00%');
  assert.equal(state.properties.get('--pointer-y'), '0.00%');
  assert.equal(state.properties.get('--drift-x'), '-9.00px');
  assert.equal(state.properties.get('--drift-y'), '-6.00px');
  state.move(5000, 5000);
  state.flush();
  assert.equal(state.properties.get('--pointer-x'), '100.00%');
  assert.equal(state.properties.get('--pointer-y'), '100.00%');
  assert.equal(state.properties.get('--drift-x'), '9.00px');
  assert.equal(state.properties.get('--drift-y'), '6.00px');
});

for (const [name, options, pointerType] of [
  ['coarse pointer', { fine: false }, 'mouse'],
  ['reduced motion', { reduced: true }, 'mouse'],
  ['hidden page', { hidden: true }, 'mouse'],
  ['touch input', {}, 'touch'],
]) {
  test('does not schedule motion for ' + name, () => {
    const state = harness(options);
    state.move(700, 500, pointerType);
    assert.equal(state.frames.size, 0);
    assert.equal(state.properties.size, 0);
  });
}

test('zero-sized decorative scenes do not write invalid CSS values', () => {
  for (const bounds of [{ width: 0, height: 600 }, { width: 1000, height: 0 }]) {
    const state = harness({ bounds: { left: 0, top: 0, ...bounds } });
    state.move();
    state.flush();
    assert.equal(state.properties.size, 0);
  }
});

for (const [scope, event] of [
  ['surface', 'pointerleave'], ['surface', 'pointercancel'], ['window', 'pagehide'],
  ['document', 'visibilitychange'], ['fine', 'change'], ['reduced', 'change'],
]) {
  test(scope + ':' + event + ' clears visuals and cancels pending animation', () => {
    const state = harness();
    state.move(700, 500);
    state.flush();
    assert.equal(state.properties.size, 5);
    state.move();
    assert.equal(state.frames.size, 1);
    state.emit(scope, event);
    assert.equal(state.frames.size, 0);
    assert.equal(state.properties.size, 0);
    state.flush();
    assert.equal(state.properties.size, 0);
  });
}

test('render rechecks accessibility and visibility preferences before painting', () => {
  const state = harness();
  state.move();
  state.reduced.matches = true;
  state.flush();
  assert.equal(state.properties.size, 0);
});

test('pointer interaction resumes cleanly after leaving and re-entering', () => {
  const state = harness();
  state.move();
  state.flush();
  state.emit('surface', 'pointerleave');
  state.move(850, 500);
  state.flush();
  assert.equal(state.properties.get('--pointer-x'), '75.00%');
  assert.equal(state.properties.get('--pointer-y'), '75.00%');
  assert.equal(state.properties.get('--pointer-active'), '1');
});
