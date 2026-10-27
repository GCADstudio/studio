// The source editor: CodeMirror 6 with a tokenizer built from the kernel's own grammar (FR-K5),
// inline diagnostics (FR-U5) and sliders on numeric literals (FR-U8).
import {
  EditorState, StateEffect, StateField, Compartment,
  EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection,
  dropCursor, rectangularSelection, crosshairCursor, highlightSpecialChars, showTooltip,
  defaultKeymap, history, historyKeymap, indentWithTab,
  StreamLanguage, HighlightStyle, syntaxHighlighting, bracketMatching, indentOnInput,
  closeBrackets, closeBracketsKeymap, searchKeymap, highlightSelectionMatches,
  lintGutter, setDiagnostics, tags,
} from "../vendor/codemirror.js";

// ---------- language from the kernel description ----------

// Anchored, not sticky: a sticky RegExp keeps lastIndex between calls, and CodeMirror's
// stream.match() expects a pattern that matches at the current position only.
function compile(source) {
  try {
    return new RegExp(`^(?:${source})`);
  } catch {
    return null;
  }
}

export function languageFor(kernel) {
  const h = kernel.highlight || {};
  const patterns = h.patterns || {};
  const keywords = new Set(h.keywords || []);
  const builtins = new Set(kernel.builtins || []);
  const operators = [...(h.operators || [])].sort((a, b) => b.length - a.length);
  const lineComments = [];
  let block = null;
  for (const source of patterns.comment || []) {
    if (source.startsWith("\\/\\*")) block = { open: "/*", close: "*/" };
    else {
      const re = compile(source);
      if (re) lineComments.push(re);
    }
  }
  if (!lineComments.length && kernel.comment) {
    lineComments.push(compile(kernel.comment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ".*"));
  }
  const ordered = [
    ["string", patterns.string],
    ["text", patterns.text],
    ["atom", patterns.keyword],
    ["number", patterns.number],
    ["symbol", patterns.symbol],
  ].flatMap(([kind, list]) =>
    (list || []).filter((p) => !p.includes("\\n") || kind !== "symbol").map((p) => [kind, compile(p)]),
  ).filter(([, re]) => re);

  const parser = {
    name: kernel.id,
    startState: () => ({ inBlock: false }),
    token(stream, state) {
      if (state.inBlock) {
        if (stream.skipTo(block.close)) {
          stream.pos += block.close.length;
          state.inBlock = false;
        } else stream.skipToEnd();
        return "comment";
      }
      if (stream.eatSpace()) return null;
      if (block && stream.match(block.open)) {
        state.inBlock = true;
        return "comment";
      }
      for (const re of lineComments) if (stream.match(re)) return "comment";
      for (const [kind, re] of ordered) {
        if (!stream.match(re)) continue;
        if (kind !== "symbol") return kind === "text" ? "string" : kind;
        const word = stream.current();
        if (keywords.has(word)) return "keyword";
        if (builtins.has(word)) return "fn";
        if (word.startsWith("$")) return "atom";
        return "variableName";
      }
      for (const op of operators) {
        if (stream.match(op)) return /^[A-Za-z_]/.test(op) ? "keyword" : (/^[()[\]{},;]$/.test(op) ? "bracket" : "operator");
      }
      stream.next();
      return null;
    },
    tokenTable: { fn: tags.function(tags.variableName), atom: tags.atom, bracket: tags.bracket },
    languageData: { commentTokens: kernel.comment ? { line: kernel.comment } : {} },
  };
  return StreamLanguage.define(parser);
}

const highlight = HighlightStyle.define([
  { tag: tags.keyword, color: "var(--syn-keyword)", fontWeight: "500" },
  { tag: tags.function(tags.variableName), color: "var(--syn-fn)" },
  { tag: tags.number, color: "var(--syn-number)" },
  { tag: tags.string, color: "var(--syn-string)" },
  { tag: tags.comment, color: "var(--syn-comment)", fontStyle: "italic" },
  { tag: tags.operator, color: "var(--syn-operator)" },
  { tag: tags.bracket, color: "var(--muted)" },
  { tag: tags.atom, color: "var(--syn-atom)" },
  { tag: tags.variableName, color: "var(--syn-var)" },
]);

const theme = EditorView.theme({
  "&": { backgroundColor: "var(--panel)", color: "var(--text)" },
  ".cm-content": { caretColor: "var(--accent)", padding: "12px 0" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)", borderLeftWidth: "2px" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "var(--selection) !important" },
  ".cm-gutters": { backgroundColor: "var(--panel)", color: "var(--muted)", border: "none", paddingLeft: "6px" },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 10px 0 8px", minWidth: "34px", fontVariantNumeric: "tabular-nums" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--panel-3) 55%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--text-2)" },
  ".cm-matchingBracket": { backgroundColor: "var(--accent-soft)", outline: "1px solid color-mix(in srgb, var(--accent) 45%, transparent)" },
  ".cm-selectionMatch": { backgroundColor: "var(--accent-soft)" },
  ".cm-tooltip": { backgroundColor: "var(--panel)", border: "1px solid var(--border-strong)", color: "var(--text)", borderRadius: "8px" },
  ".cm-diagnostic": { fontFamily: "var(--font-ui)", padding: "6px 10px" },
  ".cm-lintRange-error": { backgroundImage: "none", textDecoration: "underline wavy var(--error)", textUnderlineOffset: "3px" },
  ".cm-lintRange-warning": { backgroundImage: "none", textDecoration: "underline wavy var(--warn)", textUnderlineOffset: "3px" },
  ".cm-lintRange-info": { backgroundImage: "none" },
  ".cm-flash": { backgroundColor: "var(--accent-soft)", borderRadius: "3px", transition: "background-color .6s" },
});

// ---------- number sliders ----------

const NUMBER = /-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/g;
const openScrub = StateEffect.define();
const closeScrub = StateEffect.define();

function numberAt(state, pos) {
  const line = state.doc.lineAt(pos);
  NUMBER.lastIndex = 0;
  let m;
  while ((m = NUMBER.exec(line.text))) {
    const from = line.from + m.index;
    const to = from + m[0].length;
    const before = line.text[m.index - 1] || "";
    if (/[\w$.]/.test(before)) continue; // part of a name like r1 or $fn
    if (pos >= from && pos <= to) return { from, to, text: m[0] };
  }
  return null;
}

function decimals(text) {
  const dot = text.indexOf(".");
  return dot < 0 ? 0 : text.length - dot - 1;
}

// The field holds the number being scrubbed and its tooltip. The tooltip object is created
// once per opening: CodeMirror compares tooltips by identity, and a new object would rebuild
// the slider in the middle of a drag.
const scrubField = StateField.define({
  create: () => null,
  update(value, tr) {
    for (const e of tr.effects) {
      if (e.is(openScrub)) {
        const v = { ...e.value };
        v.tooltip = { pos: v.from, above: false, strictSide: false, create: (view) => scrubTooltip(view, v) };
        return v;
      }
      if (e.is(closeScrub)) return null;
    }
    if (!value) return null;
    if (tr.docChanged) {
      if (!tr.isUserEvent("input.scrub")) return null;
      // The number keeps its start; only its end moves as digits are added or removed.
      value.from = tr.changes.mapPos(value.from, -1);
      value.to = tr.changes.mapPos(value.to, 1);
      return value;
    }
    const head = tr.state.selection.main.head;
    return head >= value.from && head <= value.to ? value : null;
  },
  provide: (f) => showTooltip.compute([f], (state) => state.field(f)?.tooltip ?? null),
});

function scrubTooltip(view, v) {
  const dom = document.createElement("div");
  dom.className = "number-scrub";
  const range = document.createElement("input");
  range.type = "range";
  const base = Number(v.text);
  let places = decimals(v.text);
  let lo, hi;
  if (base === 0) [lo, hi] = [-10, 10];
  else if (base > 0) [lo, hi] = [0, base * 2];
  else [lo, hi] = [base * 2, -base * 2];
  if (places === 0 && hi - lo < 10) places = 1;
  range.min = String(lo);
  range.max = String(hi);
  range.step = String(10 ** -places);
  range.value = String(base);
  const value = document.createElement("span");
  value.className = "value";
  value.textContent = v.text;
  const hint = document.createElement("span");
  hint.className = "hint";
  hint.textContent = "drag";
  range.addEventListener("input", () => {
    const current = view.state.field(scrubField);
    if (!current) return;
    const text = Number(range.value).toFixed(places).replace(/^-0(\.0*)?$/, "0");
    value.textContent = text;
    view.dispatch({ changes: { from: current.from, to: current.to, insert: text }, userEvent: "input.scrub" });
  });
  range.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { view.dispatch({ effects: closeScrub.of(null) }); view.focus(); }
  });
  dom.append(range, value, hint);
  return { dom, offset: { x: 0, y: 6 } };
}

const scrubOnClick = EditorView.domEventHandlers({
  mouseup(event, view) {
    if (event.button !== 0) return false;
    setTimeout(() => {
      const sel = view.state.selection.main;
      if (!sel.empty) return;
      const n = numberAt(view.state, sel.head);
      view.dispatch({ effects: n ? openScrub.of(n) : closeScrub.of(null) });
    }, 0);
    return false;
  },
});

// ---------- the editor ----------

export function createEditor(parent, { onChange, onRun, onSave, onOpen, onExport }) {
  const language = new Compartment();
  const readOnly = new Compartment();
  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc: "",
      extensions: [
        lineNumbers(), highlightActiveLineGutter(), highlightSpecialChars(), history(), drawSelection(),
        dropCursor(), indentOnInput(), bracketMatching(), closeBrackets(), rectangularSelection(),
        crosshairCursor(), highlightActiveLine(), highlightSelectionMatches(), lintGutter(),
        syntaxHighlighting(highlight), theme, scrubField, scrubOnClick, EditorView.lineWrapping,
        language.of([]), readOnly.of(EditorState.readOnly.of(false)),
        keymap.of([
          { key: "F5", run: () => (onRun(), true) },
          { key: "Mod-Enter", run: () => (onRun(), true) },
          { key: "Mod-s", run: () => (onSave(), true), preventDefault: true },
          { key: "Mod-o", run: () => (onOpen(), true), preventDefault: true },
          { key: "Mod-e", run: () => (onExport(), true), preventDefault: true },
          { key: "Escape", run: (v) => v.state.field(scrubField) ? (v.dispatch({ effects: closeScrub.of(null) }), true) : false },
          ...closeBracketsKeymap, ...defaultKeymap, ...searchKeymap, ...historyKeymap, indentWithTab,
        ]),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) onChange(u.state.doc.toString(), u.transactions.some((t) => t.isUserEvent("input.scrub")));
        }),
      ],
    }),
  });

  return {
    view,
    load(text, kernel) {
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: text },
        effects: [language.reconfigure(languageFor(kernel)), closeScrub.of(null)],
        selection: { anchor: 0 },
        scrollIntoView: true,
      });
    },
    setLanguage(kernel) {
      view.dispatch({ effects: language.reconfigure(languageFor(kernel)) });
    },
    text: () => view.state.doc.toString(),
    setDiagnostics(list) {
      const size = view.state.doc.length;
      const diagnostics = list
        .filter((d) => d.inFile && d.from !== undefined)
        .map((d) => ({
          from: Math.min(d.from, size),
          to: Math.min(Math.max(d.to, d.from + 1), size),
          severity: d.severity === "error" ? "error" : d.severity === "warning" ? "warning" : "info",
          message: d.message,
        }));
      view.dispatch(setDiagnostics(view.state, diagnostics));
    },
    reveal(from, to = from) {
      const size = view.state.doc.length;
      const a = Math.min(from, size), b = Math.min(to, size);
      view.dispatch({ selection: { anchor: a, head: b }, scrollIntoView: true });
      view.focus();
    },
    revealLine(line) {
      const l = view.state.doc.line(Math.max(1, Math.min(line, view.state.doc.lines)));
      view.dispatch({ selection: { anchor: l.from, head: l.to }, scrollIntoView: true });
      view.focus();
    },
    lineText(pos) {
      return view.state.doc.lineAt(Math.min(pos, view.state.doc.length)).text.trim();
    },
  };
}
