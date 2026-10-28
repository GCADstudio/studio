// The annotation panel (FR-U11): what the model says about its geometry, from the kernel's
// `annotate` calls. For a STEP file that is its PMI: saved views, callouts drawn in the viewer
// (each can be hidden), and the tolerances, dimensions and datums they present. A row opens its
// `#n` in the editor and lights up the faces it applies to and the callouts that show it.

const SECTIONS = [
  ["Annotations", ["callout", "note"]],
  ["Tolerances", ["tolerance"]],
  ["Dimensions", ["dimension"]],
  ["Datums", ["datum", "datum feature", "datum target", "datum system"]],
];

export function createAnnotationPanel(root, { viewer, editor }) {
  const state = { list: [], byId: new Map(), view: null, selected: null, hidden: new Set(), key: "" };
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };

  function related(a) {
    // the annotation, what it links to, and what links to it: one level each way
    const ids = new Set([a.id, ...(a.links || [])]);
    for (const b of state.list) if ((b.links || []).includes(a.id) && b.kind !== "view") ids.add(b.id);
    return [...ids].map((id) => state.byId.get(id)).filter(Boolean);
  }

  function emphasise(a) {
    if (!a) { viewer.emphasise([], []); return; }
    const near = related(a);
    const faces = near.flatMap((b) => (b.targets || []).map((t) => t.name));
    viewer.emphasise(near.map((b) => b.id), faces);
  }

  function visibleIn(a, view) {
    return !view || (a.views || []).includes(view.id) || (view.links || []).includes(a.id);
  }

  function applyVisibility() {
    for (const a of state.list) {
      if (!a.lines && !a.triangles) continue;
      viewer.setAnnotationVisible(a.id, !state.hidden.has(a.id) && visibleIn(a, state.view));
    }
  }

  function reveal(span) {
    if (span && span.inFile !== false) editor.reveal(span.start, span.end);
  }

  function select(a, row, { jump = true } = {}) {
    state.selected = a ? a.id : null;
    for (const r of root.querySelectorAll(".pmi-row")) r.classList.toggle("active", r === row);
    emphasise(a);
    showDetails(a);
    if (a && jump) reveal(a.span);
  }

  function showDetails(a) {
    const box = root.querySelector(".pmi-details");
    box.replaceChildren();
    box.hidden = !a;
    if (!a) return;
    const dl = el("dl");
    for (const [k, v] of a.details || []) dl.append(el("dt", "", k), el("dd", "", v));
    if (a.targets?.length) {
      dl.append(el("dt", "", "faces"));
      const dd = el("dd", "targets");
      for (const t of a.targets) {
        const chip = el("button", "chip", t.name);
        chip.title = "Show this face's line in the source";
        chip.addEventListener("click", () => { reveal(t.span); viewer.emphasise([a.id], [t.name]); });
        dd.append(chip);
      }
      dl.append(dd);
    }
    box.append(dl);
  }

  function row(a) {
    const r = el("div", "pmi-row");
    r.dataset.id = a.id;
    if (a.lines || a.triangles) {
      const box = el("input");
      box.type = "checkbox";
      box.checked = !state.hidden.has(a.id);
      box.title = "Show in the viewer";
      box.addEventListener("click", (e) => e.stopPropagation());
      box.addEventListener("change", () => {
        if (box.checked) state.hidden.delete(a.id); else state.hidden.add(a.id);
        applyVisibility();
      });
      r.append(box);
    } else {
      r.append(el("span", "pmi-kind", a.kind === "datum system" ? "⌸" : a.kind.startsWith("datum") ? "◇" : "·"));
    }
    const label = el("span", "pmi-label", a.label);
    label.title = a.label;
    const ref = el("span", "pmi-ref", a.id);
    r.append(label, ref);
    r.addEventListener("click", () => select(state.selected === a.id ? null : a, state.selected === a.id ? null : r));
    r.addEventListener("mouseenter", () => { if (!state.selected) emphasise(a); });
    r.addEventListener("mouseleave", () => { if (!state.selected) emphasise(null); });
    return r;
  }

  function render() {
    root.replaceChildren();
    const views = state.list.filter((a) => a.kind === "view");
    const head = el("div", "pmi-head");
    head.append(el("strong", "", "PMI"), el("span", "muted", `${state.list.length - views.length}`));
    const all = el("input");
    all.type = "checkbox";
    all.checked = state.hidden.size === 0;
    all.title = "Show or hide every annotation";
    all.addEventListener("change", () => {
      state.hidden = all.checked ? new Set() : new Set(state.list.filter((a) => a.lines || a.triangles).map((a) => a.id));
      render();
      applyVisibility();
    });
    const collapse = el("button", "icon-btn small pmi-collapse", "–");
    collapse.title = "Collapse";
    collapse.addEventListener("click", () => root.classList.toggle("collapsed"));
    head.append(el("span", "spacer"), all, collapse);
    root.append(head);

    const body = el("div", "pmi-body");
    if (views.length) {
      const chips = el("div", "pmi-views");
      const make = (label, view) => {
        const chip = el("button", "chip" + (state.view?.id === view?.id ? " active" : ""), label);
        chip.title = view ? `Saved view ${view.id}: show its annotations and look along it` : "Every annotation";
        chip.addEventListener("click", () => {
          state.view = view;
          for (const c of chips.children) c.classList.toggle("active", c === chip);
          applyVisibility();
          if (view) { viewer.lookAlong(view.camera); reveal(view.span); }
        });
        return chip;
      };
      chips.append(make("All", null), ...views.map((v) => make(v.label, v)));
      body.append(chips);
    }
    for (const [title, kinds] of SECTIONS) {
      const items = state.list.filter((a) => kinds.includes(a.kind));
      if (!items.length) continue;
      const section = el("details", "pmi-section");
      section.open = title !== "Datums";
      section.append(el("summary", "", `${title} (${items.length})`));
      for (const a of items) section.append(row(a));
      body.append(section);
    }
    const details = el("div", "pmi-details");
    details.hidden = true;
    body.append(details);
    root.append(body);
  }

  return {
    // Called after every evaluation. A new file (another set of ids) starts afresh; a re-run of
    // the same one keeps what was hidden, the view and the selection.
    update(list) {
      list = list || [];
      const key = list.map((a) => a.id).join(" ");
      if (key !== state.key) {
        state.key = key;
        state.view = null;
        state.selected = null;
        state.hidden = new Set();
      }
      state.list = list;
      state.byId = new Map(list.map((a) => [a.id, a]));
      if (state.view) state.view = state.byId.get(state.view.id) || null;
      root.hidden = list.length === 0;
      if (!list.length) return;
      render();
      applyVisibility();
      if (state.selected && state.byId.has(state.selected)) {
        const r = root.querySelector(`.pmi-row[data-id="${CSS.escape(state.selected)}"]`);
        select(state.byId.get(state.selected), r, { jump: false });
      }
    },
    // A face clicked in the viewer: the rows that apply to it.
    pickEntity(entity) {
      for (const r of root.querySelectorAll(".pmi-row")) {
        const a = state.byId.get(r.dataset.id);
        r.classList.toggle("hit", !!entity && (a?.targets || []).some((t) => t.name === entity));
      }
    },
  };
}
