// The web studio's Python side (SPEC §4.1): Pyodide, Manifold's WebAssembly build and the
// gcad wheel in a module Web Worker. Only the static-site build has the ../pyodide and
// ../manifold folders and site.json; `gcad serve` never starts this worker.
import { loadPyodide } from "../pyodide/pyodide.mjs";
import Manifold from "../manifold/manifold.js";

const site = new URL("../", import.meta.url);
const ROOT = "/home/pyodide/studio"; // user files, backed by IndexedDB
const status = (text) => postMessage({ type: "status", text });
let pyodide = null;
let handle = null;

function syncfs(populate) {
  return new Promise((resolve) => pyodide.FS.syncfs(populate, (error) => {
    if (error) status(`Browser storage is unavailable; files will not be kept (${error})`);
    resolve();
  }));
}

async function boot() {
  const manifest = await (await fetch(new URL("site.json", site))).json();
  status("Starting Python");
  let manifold;
  [pyodide, manifold] = await Promise.all([
    loadPyodide({ indexURL: new URL("pyodide/", site).href }),
    Manifold().then((m) => { m.setup(); return m; }),
  ]);
  pyodide.registerJsModule("_gcad_manifold", manifold);
  status("Loading numpy and friends");
  // Pyodide checks each package against the SHA-256 in its pinned lock file.
  await pyodide.loadPackage(manifest.pyodidePackages, { messageCallback: () => {} });
  status("Installing GCAD Studio");
  const wheels = manifest.wheels.map((w) => new URL(w, site).href);
  await pyodide.pyimport("micropip").install.callKwargs(pyodide.toPy(wheels), { deps: false });
  const { FS } = pyodide;
  FS.mkdirTree(ROOT);
  FS.mount(FS.filesystems.IDBFS, {}, ROOT);
  const browser = pyodide.pyimport("gcad.browser");
  browser.start(ROOT);
  handle = browser.handle;
  postMessage({ type: "booted" });
}

onmessage = async ({ data }) => {
  if (data.type === "activate") {
    await syncfs(true); // the user's files as they are now
    postMessage({ type: "ready" });
    return;
  }
  let response;
  let fatal = false;
  try {
    response = handle(data.request);
    if (data.method === "file/save") await syncfs(false);
  } catch (error) {
    // gcad answers its own errors inside the response; reaching here means Python itself
    // failed (a stack overflow, say), so the page replaces this worker.
    fatal = true;
    response = JSON.stringify({ jsonrpc: "2.0", id: data.id, error: { code: -32603, message: String(error.message || error) } });
  }
  postMessage({ type: "response", id: data.id, response, fatal });
};

boot().catch((error) => postMessage({ type: "failed", message: String(error.message || error) }));
