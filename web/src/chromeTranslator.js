// Keep models alive across React renders and Settings closes. Only an explicit
// prepare click may download a model; queued translations never trigger downloads.
export function createChromeTranslator(getAPI = () => globalThis.Translator) {
  const models = new Map(), preparing = new Map(), states = new Map();
  const listeners = new Set(), jobs = new Map();
  const key = (source, target) => `${source}:${target}`;
  const options = (source, target) => ({ sourceLanguage: source, targetLanguage: target });
  const notify = (id, state) => {
    states.set(id, state);
    listeners.forEach((listener) => listener());
  };
  const problem = (code) => Object.assign(new Error(code), { code });
  function api(source, target) {
    const value = getAPI();
    if (!value?.create || !value?.availability) throw problem("unsupported");
    if (!source || !target || source === target) throw problem("languages");
    return value;
  }
  function prepare(source, target) {
    const id = key(source, target);
    if (models.has(id)) return Promise.resolve(models.get(id));
    if (preparing.has(id)) return preparing.get(id);
    let operation;
    try {
      // Call create directly in the click handler, before any await, preserving
      // the browser's transient user activation for model downloads.
      const provider = api(source, target);
      notify(id, { status: "preparing", progress: 0 });
      operation = provider.create({ ...options(source, target), monitor(monitor) {
        monitor.addEventListener("downloadprogress", (event) => {
          notify(id, { status: "preparing", progress: Math.round(Math.max(0, Math.min(1, event.loaded)) * 100) });
        });
      } });
    } catch (error) {
      operation = Promise.reject(error);
    }
    const pending = Promise.resolve(operation).then((model) => {
      models.set(id, model);
      notify(id, { status: "ready" });
      return model;
    }).catch((error) => {
      const code = error.code || (error.name === "NotSupportedError" ? "unavailable" : "prepare");
      notify(id, { status: "error", code });
      throw problem(code);
    }).finally(() => preparing.delete(id));
    preparing.set(id, pending);
    return pending;
  }
  async function ready(source, target) {
    const id = key(source, target);
    if (models.has(id)) return models.get(id);
    if (preparing.has(id)) throw problem("preparing");
    const available = await api(source, target).availability(options(source, target));
    if (available === "unavailable") throw problem("unavailable");
    if (available !== "available") throw problem("setup");
    // Restore a previously downloaded model after reload, without downloading.
    return prepare(source, target);
  }
  return {
    version: 1,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    state(source, target) { return states.get(key(source, target)) || { status: "idle" }; },
    has(source, target) { return models.has(key(source, target)); },
    prepare,
    async availability(source, target) {
      return api(source, target).availability(options(source, target));
    },
    start({ id, text, source, target }) {
      if (jobs.has(id)) throw problem("duplicate");
      const controller = new AbortController();
      const job = { status: "working", controller };
      jobs.set(id, job);
      ready(source, target).then((model) => {
        if (controller.signal.aborted) throw problem("cancelled");
        return model.translate(text, { signal: controller.signal });
      }).then((result) => {
        if (typeof result !== "string" || !result.trim()) throw problem("empty");
        job.status = "done";
        job.text = result.trim();
      }).catch((error) => {
        job.status = "error";
        job.code = controller.signal.aborted ? "cancelled" : (error.code || "translate");
      });
    },
    read(id) {
      const job = jobs.get(id);
      return job ? { status: job.status, text: job.text, code: job.code } : null;
    },
    release(id) {
      const job = jobs.get(id);
      if (job?.status === "working") job.controller.abort();
      jobs.delete(id);
    },
  };
}

export const chromeTranslator = createChromeTranslator();
