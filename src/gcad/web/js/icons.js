// Stroke icons on a 24-unit grid, used as CSS masks so they follow the text colour.
const PATHS = {
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  chevron: '<path d="m7 10 5 5 5-5"/>',
  "chevron-down": '<path d="m7 10 5 5 5-5"/>',
  code: '<path d="m9 8-5 4 5 4M15 8l5 4-5 4"/>',
  play: '<path d="M7 5v14l11-7z" fill="black"/>',
  download: '<path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14"/>',
  theme: '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 0 0 16z" fill="black"/>',
  fit: '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/>',
  cube: '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>',
  edges: '<path d="M5 8 12 4l7 4v8l-7 4-7-4z"/><path d="M5 8l7 4 7-4M12 12v8" stroke-dasharray="2 2"/>',
  wire: '<path d="M4 6h16v12H4zM4 12h16M10 6v12M16 6v12"/>',
  grid: '<path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  book: '<path d="M5 4h10a4 4 0 0 1 4 4v12H9a4 4 0 0 1-4-4z"/><path d="M5 16a4 4 0 0 1 4-4h10"/>',
  error: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.5"/>',
  warning: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4M12 17v.5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  check: '<path d="m5 12 4.5 4.5L19 7"/>',
};

export function iconUrl(name) {
  const body = PATHS[name] || PATHS.file;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="black" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
}

export function icon(name, extraClass = "") {
  const span = document.createElement("span");
  span.className = `icon ${extraClass}`.trim();
  span.style.setProperty("--i", iconUrl(name));
  return span;
}

export function hydrateIcons(root = document) {
  for (const el of root.querySelectorAll("[data-icon]")) {
    el.classList.add("icon");
    el.style.setProperty("--i", iconUrl(el.dataset.icon));
  }
}
