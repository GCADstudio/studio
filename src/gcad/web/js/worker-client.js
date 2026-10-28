// The page's side of the web studio (SPEC §4.1): JSON-RPC requests go to the Python worker
// by postMessage, one at a time. A newer evaluation replaces older ones (FR-W5): those still
// waiting are dropped at once, and one still running after GRACE_MS is stopped by replacing
// the worker, because a busy worker cannot be signalled without SharedArrayBuffer. Starting
// Python takes seconds, so a second worker is kept booted in reserve and takes over at once.
const GRACE_MS = 1000;
const STANDBY_DELAY_MS = 1500; // let the first model draw before booting the reserve
const EVALUATE = "document/evaluate";
const SUPERSEDED = { ok: false, superseded: true, diagnostics: [], ms: 0 };

export class WorkerTransport {
  constructor(onStatus = () => {}) {
    this.onStatus = onStatus;
    this.queue = [];
    this.running = null;
    this.nextId = 1;
    this.failure = null;
    this.restarts = 0;
    this.standby = null;
    this.active = this.spawn();
    this.activate(this.active);
  }

  // A worker boots Python and reports "booted"; it serves requests once activated, which
  // first reloads the user's files from IndexedDB (a reserve may have booted long before).
  spawn() {
    const slot = { worker: new Worker(new URL("./worker.js", import.meta.url), { type: "module" }), booted: false, ready: false, activating: false };
    slot.worker.onmessage = (e) => this.receive(slot, e.data);
    slot.worker.onerror = (e) => {
      if (slot === this.active) this.fail(e.message || "the Python worker could not start");
      else if (slot === this.standby) this.standby = null;
    };
    return slot;
  }

  activate(slot) {
    slot.activating = true;
    if (slot.booted) slot.worker.postMessage({ type: "activate" });
  }

  restart() {
    this.active.worker.terminate();
    this.restarts++;
    this.active = this.standby || this.spawn();
    this.standby = null;
    this.activate(this.active);
  }

  call(method, params) {
    if (this.failure) return Promise.reject(new Error(this.failure));
    return new Promise((resolve, reject) => {
      if (method === EVALUATE) {
        this.queue = this.queue.filter((job) => job.method !== EVALUATE || void job.resolve(SUPERSEDED));
        if (this.running?.method === EVALUATE) this.supersede(this.running);
      }
      this.queue.push({ id: this.nextId++, method, params, resolve, reject });
      this.pump();
    });
  }

  pump() {
    if (!this.active.ready || this.running || !this.queue.length) return;
    const job = (this.running = this.queue.shift());
    const request = JSON.stringify({ jsonrpc: "2.0", id: job.id, method: job.method, params: job.params });
    // The kernel tells the worker what to load first (OpenCASCADE for STEP files).
    this.active.worker.postMessage({ type: "request", id: job.id, method: job.method, kernel: job.params?.kernel, request });
  }

  supersede(job) {
    job.timer ??= setTimeout(() => {
      if (this.running !== job) return;
      this.running = null;
      job.resolve(SUPERSEDED);
      this.restart();
    }, GRACE_MS);
  }

  receive(slot, message) {
    if (message.type === "booted") {
      slot.booted = true;
      if (slot.activating) this.activate(slot);
    } else if (slot !== this.active) {
      if (message.type === "failed") this.standby = null; // try again after the next restart
    } else if (message.type === "status") this.onStatus(message.text);
    else if (message.type === "ready") {
      slot.ready = true;
      setTimeout(() => { if (!this.standby && slot === this.active) this.standby = this.spawn(); }, STANDBY_DELAY_MS);
      this.pump();
    } else if (message.type === "failed") this.fail(message.message);
    else if (message.type === "response") {
      const job = this.running;
      if (!job || job.id !== message.id) return;
      clearTimeout(job.timer);
      this.running = null;
      const body = JSON.parse(message.response);
      if (body.error) job.reject(new Error(body.error.message));
      else job.resolve(body.result);
      if (message.fatal) this.restart(); // Python cannot continue after a fatal error
      this.pump();
    }
  }

  fail(reason) {
    this.failure = `The Python worker failed: ${reason}`;
    for (const job of [this.running, ...this.queue]) job?.reject(new Error(this.failure));
    this.running = null;
    this.queue = [];
  }
}
