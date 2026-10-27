// The CodeMirror 6 pieces GCAD Studio uses, bundled into one ES module so the studio works
// offline. Rebuild with `npm ci && npm run build` in this folder.
export { EditorState, StateEffect, StateField, Compartment, RangeSetBuilder } from "@codemirror/state";
export {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
  highlightActiveLineGutter,
  drawSelection,
  dropCursor,
  rectangularSelection,
  crosshairCursor,
  highlightSpecialChars,
  Decoration,
  ViewPlugin,
  WidgetType,
  showTooltip,
  hoverTooltip,
} from "@codemirror/view";
export { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
export {
  StreamLanguage,
  HighlightStyle,
  syntaxHighlighting,
  bracketMatching,
  indentOnInput,
  foldGutter,
  foldKeymap,
} from "@codemirror/language";
export { closeBrackets, closeBracketsKeymap, autocompletion, completionKeymap } from "@codemirror/autocomplete";
export { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
export { linter, lintGutter, setDiagnostics } from "@codemirror/lint";
export { tags } from "@lezer/highlight";
