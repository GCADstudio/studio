// GCAD Studio front end: wires the editor, the viewer and the server together.
import { rpc, openOnStart, mode, onTransportStatus } from "./rpc.js";
import { hydrateIcons, icon } from "./icons.js";
import { createEditor } from "./editor.js";
import { Viewer } from "./viewer.js";

const $ = (sel) => document.querySelector(sel);
const LIVE_DELAY = 380;
const SCRUB_DELAY = 70;
const DEFAULT_FILE = "example:minscad/examples/bracket.mscad";
const WEB = mode === "web"; // the Python core runs in this tab (SPEC §4.1)
const store = {
  get(key) { try { return localStorage.getItem(`gcad.${key}`); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(`gcad.${key}`, value); } catch { /* private mode */ } },
};

const state = {
  kernels: new Map(),
  file: { path: null, name: "untitled", kernel: "minscad", readOnly: false },
  dirty: false,
  seq: 0,
  shown: 0,
  timer: null,
  result: null,
  formats: [],
  root: "",
};

hydrateIcons();

// ---------- editor and viewer ----------

const editor = createEditor($("#editor"), {
  onChange(text, scrubbing) {
    setDirty(true);
    if ($("#live").checked) schedule(scrubbing ? SCRUB_DELAY : LIVE_DELAY);
  },
  onRun: () => run(),
  onSave: () => save(),
  onOpen: () => openPalette(),
  onExport: () => openExport(),
});

const viewer = new Viewer($("#viewer"), {
  onPick(group) {
    const card = $("#pick-card");
    if (!group || !group.spans.length) { card.hidden = true; return; }
    const span = group.spans[0];
    editor.reveal(span.start, span.end);
    card.replaceChildren();
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = group.color
      ? `rgb(${group.color.slice(0, 3).map((c) => Math.round(c * 255)).join(",")})`
      : "#a9b6cc";
    const where = document.createElement("span");
    where.className = "muted";
    where.textContent = `line ${span.line}`;
    const code = document.createElement("code");
    code.textContent = editor.lineText(span.start);
    card.append(swatch, where, code);
    card.hidden = false;
  },
});

// ---------- running ----------

function schedule(delay) {
  clearTimeout(state.timer);
  state.timer = setTimeout(run, delay);
}

async function run() {
  clearTimeout(state.timer);
  const seq = ++state.seq;
  const wantGcore = !$('[data-panel="gcore"]').hidden;
  setStatus("busy", "Running");
  let result;
  try {
    result = await rpc("document/evaluate", {
      kernel: state.file.kernel,
      source: editor.text(),
      file: state.file.name,
      gcore: wantGcore,
    });
  } catch (error) {
    if (seq === state.seq) setStatus("error", WEB ? "Python stopped" : "Server unreachable");
    log(String(error.message || error), "error");
    return;
  }
  if (seq !== state.seq) return; // a newer run is on its way
  state.result = result;
  editor.setDiagnostics(result.diagnostics);
  showProblems(result.diagnostics);
  if (wantGcore && result.gcore !== undefined) $('[data-panel="gcore"]').textContent = result.gcore;
  for (const d of result.diagnostics) if (d.severity === "info") log(d.message, "info");
  const errors = result.diagnostics.filter((d) => d.severity === "error").length;
  if (result.ok) {
    const first = state.shown === 0;
    viewer.show(result, { fit: first || state.fitNext });
    state.fitNext = false;
    state.shown++;
    $("#empty-state").hidden = true;
    showModelCard(result);
    setStatus("ok", `Ready · ${formatMs(result.ms)}`);
  } else {
    setStatus("error", errors === 1 ? "1 error" : `${errors} errors`);
  }
  $("#console-meta").textContent = result.ok ? `graph ${result.hash}` : "showing the last good model";
}

function formatMs(ms) {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function setStatus(kind, text) {
  $("#status").dataset.state = kind;
  $("#status-text").textContent = text;
}

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const fmt = (v) => number.format(v).replace(/,/g, " ");

function showModelCard(result) {
  const card = $("#model-card");
  card.replaceChildren();
  const [lo, hi] = result.bounds || [[0, 0, 0], [0, 0, 0]];
  const size = hi.map((v, i) => v - lo[i]);
  const head = document.createElement("div");
  head.className = "size";
  head.innerHTML = result.dim === 2
    ? `${fmt(size[0])} × ${fmt(size[1])} <small>mm</small>`
    : `${fmt(size[0])} × ${fmt(size[1])} × ${fmt(size[2])} <small>mm</small>`;
  card.append(head);
  const rows = result.dim === 2
    ? [["Area", `${fmt(result.area)} mm²`], ["Outlines", String(result.contours.length)]]
    : [["Volume", `${fmt(result.volume)} mm³`], ["Surface", `${fmt(result.area)} mm²`], ["Triangles", fmt(result.triangles)]];
  for (const [k, v] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    card.append(dt, dd);
  }
  card.hidden = false;
}

// ---------- console ----------

function showProblems(list) {
  const ul = $(".problems");
  ul.replaceChildren();
  const shown = list.filter((d) => d.severity !== "info");
  const badge = $("#problem-count");
  badge.textContent = String(shown.length);
  badge.className = "badge" + (shown.some((d) => d.severity === "error") ? " error" : shown.length ? " warn" : "");
  if (!shown.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No problems. Click a face in the viewer to find the code that made it; click a number in the code to drag it.";
    ul.append(li);
    return;
  }
  for (const d of shown) {
    const li = document.createElement("li");
    li.append(icon(d.severity === "error" ? "error" : d.severity === "warning" ? "warning" : "info", `sev ${d.severity}`));
    const msg = document.createElement("span");
    msg.textContent = d.message;
    const where = document.createElement("span");
    where.className = "where";
    where.textContent = d.line ? (d.inFile ? `${d.line}:${d.col}` : `${d.file}:${d.line}`) : "";
    li.append(msg, where);
    if (d.inFile) li.addEventListener("click", () => editor.reveal(d.from, d.to));
    ul.append(li);
  }
  if (shown.some((d) => d.severity === "error")) selectTab("problems");
}

function log(message, kind = "info") {
  const out = $(".output");
  const line = document.createElement("div");
  line.className = `line ${kind}`;
  const time = document.createElement("span");
  time.className = "time";
  time.textContent = new Date().toLocaleTimeString();
  const text = document.createElement("span");
  text.textContent = message;
  line.append(time, text);
  out.append(line);
  while (out.childElementCount > 300) out.firstElementChild.remove();
  out.scrollTop = out.scrollHeight;
}

function selectTab(name) {
  for (const tab of document.querySelectorAll(".tab")) tab.classList.toggle("active", tab.dataset.tab === name);
  for (const panel of document.querySelectorAll("[data-panel]")) panel.hidden = panel.dataset.panel !== name;
  if (name === "gcore") run();
}

for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => {
  $("#console").classList.remove("collapsed");
  selectTab(tab.dataset.tab);
});
$("#console-toggle").addEventListener("click", () => $("#console").classList.toggle("collapsed"));

// ---------- files ----------

function setDirty(dirty) {
  state.dirty = dirty;
  $("#dirty").hidden = !dirty;
}

function setFile(file) {
  state.file = file;
  $("#file-name").textContent = file.name;
  $("#read-only").hidden = !file.readOnly;
  $("#kernel").value = file.kernel;
  document.title = `${file.name} · GCAD Studio`;
}

async function openFile(path) {
  try {
    const file = await rpc("file/open", { path });
    setFile(file);
    editor.load(file.text, state.kernels.get(file.kernel));
    setDirty(false);
    state.fitNext = true;
    store.set("last", path);
    run();
  } catch (error) {
    toast(error.message, "error");
  }
}

function newFile(kernelId) {
  const kernel = state.kernels.get(kernelId);
  const starters = {
    minscad: "// A new minscad model\n$fn = 48;\n\ndifference() {\n    cube([30, 20, 8]);\n    translate([15, 10, -1]) cylinder(h = 10, d = 6);\n}\n",
    csg: "; A new csg model\n(difference\n  (box [30 20 8])\n  (translate [15 10 -1] (cylinder :h 10 :d 6)))\n",
    parts: "# A new parts list\npart base: box 30 20 8\ncut  hole: cylinder d 6 h 10 at 15 10 -1\n",
  };
  setFile({ path: null, name: `untitled${kernel.extensions[0]}`, kernel: kernelId, readOnly: false });
  editor.load(starters[kernelId] || "", kernel);
  setDirty(true);
  state.fitNext = true;
  run();
}

async function save() {
  if (!state.file.path || state.file.readOnly) return openSaveAs();
  try {
    await rpc("file/save", { path: state.file.path, text: editor.text() });
    setDirty(false);
    toast(`Saved ${state.file.name}`);
  } catch (error) {
    toast(error.message, "error");
  }
}

function openSaveAs() {
  const dialog = $("#save-dialog");
  const input = $("#save-input");
  $("#save-root").textContent = `in ${state.root}`;
  input.value = state.file.name;
  dialog.hidden = false;
  input.focus();
  input.setSelectionRange(0, input.value.lastIndexOf(".") > 0 ? input.value.lastIndexOf(".") : input.value.length);
}

async function confirmSaveAs() {
  const path = $("#save-input").value.trim();
  if (!path) return;
  try {
    const saved = await rpc("file/save", { path, text: editor.text() });
    $("#save-dialog").hidden = true;
    setFile({ ...state.file, path: saved.path, name: saved.name, readOnly: false });
    setDirty(false);
    store.set("last", saved.path);
    toast(`Saved ${saved.path}`);
  } catch (error) {
    toast(error.message, "error");
  }
}
$("#save-confirm").addEventListener("click", confirmSaveAs);
$("#save-input").addEventListener("keydown", (e) => { if (e.key === "Enter") confirmSaveAs(); });

// ---------- open palette ----------

let paletteItems = [];
let paletteIndex = 0;

async function openPalette() {
  const listing = await rpc("file/list");
  state.root = listing.root;
  paletteItems = [
    ...listing.examples.map((f) => ({ section: "Examples", icon: "book", name: f.name, kernel: f.kernel, run: () => openFile(f.path) })),
    ...listing.files.map((f) => ({ section: WEB ? "In this browser" : "This folder", icon: "file", name: f.path, kernel: f.kernel, run: () => openFile(f.path) })),
    ...[...state.kernels.values()].map((k) => ({ section: "New", icon: "plus", name: `New ${k.name}`, kernel: k.id, run: () => newFile(k.id) })),
  ];
  if (WEB) {
    // Files live in the browser here, so moving them to and from the disk comes first.
    paletteItems.unshift(
      { section: "Your disk", icon: "upload", name: "Upload a file…", kernel: "open from disk", run: uploadFile },
      { section: "Your disk", icon: "download", name: "Download the open file", kernel: "save a copy", run: downloadFile, keep: true },
    );
  }
  const where = WEB ? "files are kept in this browser" : `folder: ${escapeHtml(listing.root)}`;
  $("#palette-foot").innerHTML = `<span><kbd>↑</kbd> <kbd>↓</kbd> to move</span><span><kbd>Enter</kbd> to open</span><span class="muted">${where}</span>`;
  $("#palette").hidden = false;
  const input = $("#palette-input");
  input.value = "";
  renderPalette();
  input.focus();
}

function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function filteredPalette() {
  const q = $("#palette-input").value.trim().toLowerCase();
  return paletteItems.filter((i) => !q || `${i.name} ${i.kernel}`.toLowerCase().includes(q));
}

function renderPalette() {
  const list = $("#palette-list");
  list.replaceChildren();
  const items = filteredPalette();
  paletteIndex = Math.max(0, Math.min(paletteIndex, items.length - 1));
  let section = "";
  items.forEach((item, i) => {
    if (item.section !== section) {
      section = item.section;
      const head = document.createElement("li");
      head.className = "section";
      head.textContent = section;
      list.append(head);
    }
    const li = document.createElement("li");
    li.className = "item" + (i === paletteIndex ? " selected" : "");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = item.name;
    const kernel = document.createElement("span");
    kernel.className = "kernel";
    kernel.textContent = item.kernel;
    li.append(icon(item.icon), name, kernel);
    li.addEventListener("mousemove", () => { if (paletteIndex !== i) { paletteIndex = i; renderPalette(); } });
    li.addEventListener("click", () => choosePalette(i));
    list.append(li);
  });
  list.querySelector(".selected")?.scrollIntoView({ block: "nearest" });
}

function choosePalette(i) {
  const item = filteredPalette()[i];
  if (!item) return;
  $("#palette").hidden = true;
  if (!item.keep && state.dirty && !confirm("Discard unsaved changes?")) return;
  item.run();
}

function saveBlob(bytes, name, type) {
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// Web mode: files live in the browser, so these move them to and from the visitor's disk.
function downloadFile() {
  saveBlob(editor.text(), state.file.name, "text/plain");
  toast(`Downloaded ${state.file.name}`);
}

function uploadFile() {
  const input = $("#upload-input");
  // .exp: an EXPRESS schema, kept to check ISO 10303 files against (SPEC FR-I4)
  input.accept = [...[...state.kernels.values()].flatMap((k) => k.extensions), ".exp"].join(",");
  input.value = ""; // choosing the same file again still counts as a change
  input.click();
}

$("#upload-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const saved = await rpc("file/save", { path: file.name, text: await file.text() });
    if (saved.schemas) {
      toast(`Schema kept for checking STEP files: ${saved.schemas.join(", ")}`);
      return;
    }
    await openFile(saved.path);
    toast(`Uploaded ${saved.name}`);
  } catch (error) {
    toast(error.message, "error");
  }
});

$("#palette-input").addEventListener("input", () => { paletteIndex = 0; renderPalette(); });
$("#palette-input").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { paletteIndex++; renderPalette(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { paletteIndex = Math.max(0, paletteIndex - 1); renderPalette(); e.preventDefault(); }
  else if (e.key === "Enter") choosePalette(paletteIndex);
});
$("#file-button").addEventListener("click", openPalette);

// ---------- export ----------

const GROUPS = [
  { needs: "brep", title: "Exact solid", note: "B-rep through OpenCASCADE, for other CAD systems" },
  { needs: "mesh", title: "Mesh", note: "triangles, for printing, rendering and simulation" },
  { needs: "2d", title: "Drawing", note: "1:1 outlines in millimetres, for cutting and plotting" },
  { needs: "graph", title: "Parametric", note: "the construction and its parameters, for other CAD systems" },
];
const DESCRIPTIONS = {
  step: "ISO 10303, AP214/AP242", "step-parametric": "ISO 10303 CSG + parameters", iges: "IGES 5.3 solid", stl: "3D printing", obj: "Wavefront, with colours",
  glb: "glTF 2.0, web and AR", x3d: "X3D 3.3", vrml: "VRML97", dae: "COLLADA 1.4.1", unv: "FE surface mesh",
  dxf: "CAD drawing", svg: "vector graphics", pdf: "print at 1:1", eps: "PostScript", cgm: "ISO 8632 metafile",
};

async function openExport() {
  if (!state.formats.length) state.formats = await rpc("export/formats");
  const dim = state.result?.ok ? state.result.dim : null;
  $("#export-sub").textContent = dim
    ? `${state.file.name} · ${dim === 2 ? "2D drawing" : "3D model"}`
    : "Fix the errors first: there is no model to export";
  const holder = $("#export-groups");
  holder.replaceChildren();
  for (const group of GROUPS) {
    const section = document.createElement("section");
    section.className = "export-group";
    section.innerHTML = `<h3>${group.title} <span>${group.note}</span></h3>`;
    const grid = document.createElement("div");
    grid.className = "export-grid";
    const members = state.formats.filter((x) => x.needs === group.needs);
    const reasons = new Set(members.map((f) => f.reason));
    if (members.length && reasons.size === 1 && members[0].reason) {
      // The whole group is out of reach here (no OpenCASCADE): say why once, in the open.
      const note = document.createElement("p");
      note.className = "group-note";
      note.textContent = members[0].reason.replace(/^./, (c) => c.toUpperCase()) + ".";
      section.append(note);
    }
    for (const f of members) {
      const button = document.createElement("button");
      button.className = "format";
      button.innerHTML = `<b>${f.ext}</b><small>${DESCRIPTIONS[f.id] || f.name}</small>`;
      let reason = f.reason;
      if (!reason && !dim) reason = "no model";
      else if (!reason && group.needs === "2d" && dim !== 2) reason = "needs a 2D model: use projection() or section";
      else if (!reason && group.needs !== "2d" && group.needs !== "graph" && dim === 2) reason = "needs a 3D model: extrude the drawing";
      button.disabled = Boolean(reason);
      button.title = reason || f.name;
      button.addEventListener("click", () => runExport(f, button));
      grid.append(button);
    }
    section.append(grid);
    holder.append(section);
  }
  $("#export-dialog").hidden = false;
}

async function runExport(format, button) {
  button.classList.add("busy");
  try {
    const result = await rpc("export/run", {
      kernel: state.file.kernel,
      source: editor.text(),
      file: state.file.name,
      format: format.id,
      options: {
        schema: $("#opt-schema").value,
        dxf_version: $("#opt-dxf").value,
        ascii: $("#opt-ascii").checked,
      },
    });
    saveBlob(Uint8Array.from(atob(result.data), (c) => c.charCodeAt(0)), result.name, result.mime);
    toast(`Exported ${result.name} · ${fmt(result.bytes / 1024)} KB`);
    log(`exported ${result.name} (${result.bytes} bytes, back-end ${result.backend})`);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.classList.remove("busy");
  }
}

$("#export-button").addEventListener("click", openExport);

// ---------- dialogs, toasts, theme, layout ----------

for (const overlay of document.querySelectorAll(".overlay")) {
  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) overlay.hidden = true; });
  for (const close of overlay.querySelectorAll("[data-close]")) close.addEventListener("click", () => { overlay.hidden = true; });
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") for (const o of document.querySelectorAll(".overlay")) o.hidden = true;
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === "o") { e.preventDefault(); openPalette(); }
  if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); save(); }
  if (mod && e.key.toLowerCase() === "e") { e.preventDefault(); openExport(); }
  if (e.key === "F5") { e.preventDefault(); run(); }
  if (e.key.toLowerCase() === "f" && document.activeElement === document.body) viewer.fit();
});

function toast(message, kind = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.append(icon(kind === "error" ? "error" : "check"));
  const text = document.createElement("span");
  text.textContent = message;
  el.append(text);
  $("#toasts").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 6000 : 3000);
}

$("#theme-button").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  store.set("theme", next);
  viewer.refreshTheme();
});

$("#run-button").addEventListener("click", run);
$("#live").addEventListener("change", (e) => { store.set("live", e.target.checked ? "1" : "0"); if (e.target.checked) run(); });
if (store.get("live") === "0") $("#live").checked = false;

$("#kernel").addEventListener("change", (e) => {
  state.file.kernel = e.target.value;
  editor.setLanguage(state.kernels.get(state.file.kernel));
  run();
});

for (const button of document.querySelectorAll("[data-view]")) button.addEventListener("click", () => viewer.setView(button.dataset.view));
$('[data-action="fit"]').addEventListener("click", () => viewer.fit());
for (const button of document.querySelectorAll("[data-toggle]")) {
  button.addEventListener("click", () => {
    const on = !button.classList.contains("active");
    button.classList.toggle("active", on);
    viewer.set(button.dataset.toggle, on);
  });
}

function dragResize(handle, onMove) {
  handle.addEventListener("pointerdown", (e) => {
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("dragging");
    const move = (ev) => onMove(ev);
    const up = () => {
      handle.classList.remove("dragging");
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  });
}
const workspace = $("#workspace");
dragResize($("#splitter"), (e) => {
  const rect = workspace.getBoundingClientRect();
  const pct = Math.min(75, Math.max(22, ((e.clientX - rect.left) / rect.width) * 100));
  workspace.style.setProperty("--editor-w", `${pct}%`);
  store.set("editorWidth", String(pct));
});
dragResize($("#console-resize"), (e) => {
  const h = Math.min(window.innerHeight * 0.6, Math.max(90, window.innerHeight - e.clientY));
  $("#console").style.setProperty("--console-h", `${h}px`);
  $("#console").classList.remove("collapsed");
});
const savedWidth = store.get("editorWidth");
if (savedWidth) workspace.style.setProperty("--editor-w", `${savedWidth}%`);

// For automated UI checks and debugging in the browser console.
window.gcad = { viewer, editor, state, run, rpc };

// ---------- start ----------

async function start() {
  if (WEB) {
    $("#mode-chip").hidden = false;
    setStatus("busy", "Starting Python");
    onTransportStatus((text) => {
      log(text);
      if ($("#status").dataset.state !== "ok") setStatus("busy", text);
    });
  }
  try {
    const hello = await rpc("hello");
    state.root = hello.root;
    log(WEB ? `${hello.generator} · running in this browser, no server` : `${hello.generator} · folder ${hello.root}`);
    for (const [name, status] of Object.entries(hello.backends)) if (status !== "ok") log(`back-end ${name}: ${status}`, "warn");
    const kernels = await rpc("kernel/list");
    const select = $("#kernel");
    for (const k of kernels) {
      state.kernels.set(k.id, k);
      const option = document.createElement("option");
      option.value = k.id;
      option.textContent = k.name;
      select.append(option);
    }
    showProblems([]);
    rpc("export/formats").then((formats) => { state.formats = formats; }).catch(() => {});
    await openFile(openOnStart || store.get("last") || DEFAULT_FILE);
    if (!state.result && !state.file.path) await openFile(DEFAULT_FILE);
  } catch (error) {
    setStatus("error", "Not connected");
    log(String(error.message || error), "error");
    toast(error.message, "error");
  }
}

start();
