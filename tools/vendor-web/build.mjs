// Produce src/gcad/web/vendor: one CodeMirror bundle and the three.js files the viewer imports.
import { build } from "esbuild";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "..", "..", "src", "gcad", "web", "vendor");
mkdirSync(join(out, "addons"), { recursive: true });

await build({
  entryPoints: [join(here, "codemirror-entry.js")],
  bundle: true,
  format: "esm",
  minify: true,
  legalComments: "none",
  outfile: join(out, "codemirror.js"),
});

const three = join(here, "node_modules", "three");
copyFileSync(join(three, "build", "three.module.min.js"), join(out, "three.module.min.js"));
copyFileSync(join(three, "build", "three.core.min.js"), join(out, "three.core.min.js"));
for (const addon of [
  "controls/OrbitControls.js",
  "helpers/ViewHelper.js",
  "environments/RoomEnvironment.js",
  "utils/BufferGeometryUtils.js",
]) {
  const target = join(out, "addons", addon);
  mkdirSync(dirname(target), { recursive: true });
  copyFileSync(join(three, "examples", "jsm", addon), target);
}

const pkg = JSON.parse(readFileSync(join(here, "package.json"), "utf8"));
const versions = Object.entries(pkg.devDependencies)
  .filter(([name]) => name !== "esbuild")
  .map(([name, version]) => `- ${name} ${version} (MIT)`)
  .join("\n");
writeFileSync(
  join(out, "README.md"),
  `# Vendored JavaScript\n\nBuilt by \`tools/vendor-web\` (\`npm ci && npm run build\`) so the studio works offline. Do not edit by hand.\n\n${versions}\n\nthree.js and CodeMirror are MIT licensed; see LICENSE-three.txt and LICENSE-codemirror.txt.\n`,
);
copyFileSync(join(three, "LICENSE"), join(out, "LICENSE-three.txt"));
copyFileSync(join(here, "node_modules", "@codemirror", "view", "LICENSE"), join(out, "LICENSE-codemirror.txt"));
console.log("vendor bundle written to", out);
