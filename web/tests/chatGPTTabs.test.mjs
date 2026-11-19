import test from 'node:test';
import assert from 'node:assert/strict';
import { createChatGPTTabLoader } from '../src/chatGPTTabs.js';

test('existing tabs keep discovery order; none are created', async () => {
  const tabs = [{ key: 'first' }, { key: 'second' }];
  const load = createChatGPTTabLoader(async () => tabs, async () => assert.fail('unexpected creation'));
  assert.deepEqual(await load({ ensure: true }), tabs);
});

test('simultaneous requests share discovery and create only one confirmed tab', async () => {
  let scans = 0, opens = 0, finish;
  const load = createChatGPTTabLoader(async () => { scans++; return []; }, async () => {
    opens++; return new Promise(resolve => { finish = resolve; });
  });
  const a = load({ ensure: true }), b = load({ ensure: true });
  await new Promise(resolve => setTimeout(resolve, 0));
  const c = load({ ensure: true });
  finish({ key: 'new', title: 'ChatGPT' });
  assert.deepEqual(await Promise.all([a, b, c]), Array(3).fill([{ key: 'new', title: 'ChatGPT' }]));
  assert.equal(scans, 1); assert.equal(opens, 1);
});

test('failed discovery never creates a tab; failed creation can be retried', async () => {
  const failed = createChatGPTTabLoader(async () => { throw new Error('Chrome unavailable'); }, async () => assert.fail());
  await assert.rejects(failed({ ensure: true }), /Chrome unavailable/);
  let tries = 0;
  const load = createChatGPTTabLoader(async () => [], async () => {
    if (!tries++) throw new Error('Open failed');
    return { key: 'retry' };
  });
  assert.deepEqual(await load(), []);
  assert.equal(tries, 0);
  await assert.rejects(load({ ensure: true }), /Open failed/);
  assert.deepEqual(await load({ ensure: true }), [{ key: 'retry' }]);
});

test('an outdated Chat check cannot create a tab when discovery returns empty', async () => {
  const load = createChatGPTTabLoader(async () => [], async () => assert.fail('unexpected creation'));
  assert.deepEqual(await load({ ensure: true, beforeOpen: () => false }), []);
});
