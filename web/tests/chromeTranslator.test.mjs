import test from "node:test";
import assert from "node:assert/strict";
import { createChromeTranslator } from "../src/chromeTranslator.js";

const tick = () => new Promise((resolve) => setImmediate(resolve));

test("explicit preparation preserves click activation, progress, and reuses models", async () => {
  let creates = 0, progress;
  const model = { translate: async (text) => `translated: ${text}` };
  const bridge = createChromeTranslator(() => ({
    availability: async () => "downloadable",
    create(options) {
      creates++;
      options.monitor({ addEventListener(_, listener) { progress = listener; } });
      return Promise.resolve(model);
    },
  }));
  const pending = bridge.prepare("ja", "vi");
  assert.equal(creates, 1); // No async availability call before create.
  progress({ loaded: .5 });
  assert.equal(bridge.state("ja", "vi").progress, 50);
  await pending;
  await bridge.prepare("ja", "vi");
  bridge.start({ id: "a", text: "hello", source: "ja", target: "vi" });
  await tick();
  assert.equal(bridge.read("a").text, "translated: hello");
  assert.equal(creates, 1);
  bridge.release("a");
  assert.equal(bridge.read("a"), null);
});

test("background work never downloads and unsupported pairs fail clearly", async () => {
  for (const [availability, code] of [["downloadable", "setup"], ["downloading", "setup"], ["unavailable", "unavailable"]]) {
    const bridge = createChromeTranslator(() => ({
      availability: async () => availability,
      create() { assert.fail("Background work must not download"); },
    }));
    bridge.start({ id: "a", text: "hello", source: "ja", target: "vi" });
    await tick();
    assert.equal(bridge.read("a").code, code);
  }
  const unsupported = createChromeTranslator(() => undefined);
  await assert.rejects(unsupported.prepare("en", "vi"), { code: "unsupported" });
});

test("download failures are retryable; language pairs have independent models", async () => {
  let fail = true, creates = 0;
  const bridge = createChromeTranslator(() => ({
    availability: async () => "available",
    create() { creates++; return fail ? Promise.reject(new Error("download failed")) : Promise.resolve({ translate: async () => "ok" }); },
  }));
  await assert.rejects(bridge.prepare("en", "vi"), { code: "prepare" });
  fail = false;
  await bridge.prepare("en", "vi");
  bridge.start({ id: "a", text: "hello", source: "ja", target: "vi" });
  await tick();
  assert.equal(bridge.read("a").status, "done");
  assert.equal(creates, 3);
});

test("cancelling an in-flight translation aborts it and discards late output", async () => {
  let signal, finish;
  const bridge = createChromeTranslator(() => ({
    availability: async () => "available",
    create: async () => ({ translate(text, options) {
      signal = options.signal;
      return new Promise((resolve) => { finish = resolve; });
    } }),
  }));
  bridge.start({ id: "a", text: "hello", source: "en", target: "vi" });
  await tick();
  bridge.release("a");
  assert.equal(signal.aborted, true);
  finish("late result");
  await tick();
  assert.equal(bridge.read("a"), null);
});

test("concurrent jobs share initialization and keep independent results and cancellation", async () => {
  let creates = 0, initialize;
  const running = new Map();
  const model = { translate(text, { signal }) {
    return new Promise(resolve => running.set(text, { resolve, signal }));
  } };
  const bridge = createChromeTranslator(() => ({
    availability: async () => "available",
    create() { creates++; return new Promise(resolve => { initialize = () => resolve(model); }); },
  }));
  bridge.start({ id: "one", text: "slow", source: "ja", target: "vi" });
  await tick();
  bridge.start({ id: "two", text: "fast", source: "ja", target: "vi" });
  bridge.start({ id: "three", text: "cancelled", source: "ja", target: "vi" });
  initialize(); await tick();
  assert.equal(creates, 1);
  assert.equal(running.size, 3);
  running.get("fast").resolve("Fast result"); await tick();
  assert.equal(bridge.read("two").text, "Fast result");
  assert.equal(bridge.read("one").status, "working");
  bridge.release("three");
  assert.equal(running.get("cancelled").signal.aborted, true);
  assert.equal(running.get("slow").signal.aborted, false);
  running.get("slow").resolve("Slow result"); await tick();
  assert.equal(bridge.read("one").text, "Slow result");
});
