// The model viewer (FR-U4): three.js with studio lighting, crisp CAD edges, picking that maps
// a face back to the source span that made it (FR-U7), and a 2D mode for drawings (FR-U9).
// WebGL runs on any GPU or on the browser's software renderer (NFR-2).
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { ViewHelper } from "three/addons/helpers/ViewHelper.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { toCreasedNormals } from "three/addons/utils/BufferGeometryUtils.js";
import { decode } from "./rpc.js";

const DEFAULT_COLOR = new THREE.Color("#a9b6cc");
const VIEWS = {
  iso: [1, -1.15, 0.85],
  top: [0, -0.0001, 1],
  front: [0, -1, 0],
  right: [1, 0, 0],
};

function themeColors() {
  const style = getComputedStyle(document.documentElement);
  const dark = document.documentElement.dataset.theme !== "light";
  return {
    dark,
    edge: dark ? new THREE.Color("#0b0d12") : new THREE.Color("#2a3142"),
    grid: dark ? new THREE.Color("#3a4255") : new THREE.Color("#c3c9d6"),
    grid2: dark ? new THREE.Color("#232937") : new THREE.Color("#dde1ea"),
    accent: new THREE.Color(style.getPropertyValue("--accent").trim() || "#7b93ff"),
  };
}

// Annotation colours: the file's, unless it is plain black or white (a drawing's default pen),
// which would vanish on one of the themes; then the theme's ink.
function annotationInk() {
  return document.documentElement.dataset.theme === "light" ? new THREE.Color("#1d2433") : new THREE.Color("#eef1f8");
}

function annotationColor(rgba, ink) {
  if (!rgba) return ink.clone();
  const [r, g, b] = rgba;
  if ((r > 0.95 && g > 0.95 && b > 0.95) || (r < 0.05 && g < 0.05 && b < 0.05)) return ink.clone();
  return new THREE.Color().setRGB(r, g, b, THREE.SRGBColorSpace);
}

function unionBounds(a, b) {
  if (!a) return b;
  if (!b) return a;
  return [a[0].map((v, i) => Math.min(v, b[0][i])), a[1].map((v, i) => Math.max(v, b[1][i]))];
}

export class Viewer {
  constructor(container, { onPick } = {}) {
    this.container = container;
    this.onPick = onPick;
    this.options = { edges: true, wireframe: false, grid: true };

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.autoClear = false;
    container.appendChild(renderer.domElement);
    this.renderer = renderer;

    const scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environmentIntensity = 0.55;
    this.scene = scene;

    scene.add(new THREE.HemisphereLight(0xdfe6ff, 0x30343f, 0.55));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.bias = -0.0004;
    key.shadow.radius = 5;
    this.key = key;
    scene.add(key, key.target);
    const rim = new THREE.DirectionalLight(0x9fb4ff, 0.6);
    this.rim = rim;
    scene.add(rim);

    this.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1e6);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(80, -90, 70);

    this.controls = new OrbitControls(this.camera, renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.screenSpacePanning = true;

    this.viewHelper = new ViewHelper(this.camera, renderer.domElement);
    this.viewHelper.center = this.controls.target;
    this.clock = new THREE.Clock();

    this.root = new THREE.Group();
    scene.add(this.root);
    this.shadowFloor = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.ShadowMaterial({ opacity: 0.3, transparent: true }),
    );
    this.shadowFloor.receiveShadow = true;
    scene.add(this.shadowFloor);
    this.grid = null;

    this.raycaster = new THREE.Raycaster();
    this.pointerDown = null;
    this.selected = -1;
    this.hovered = -1;
    this.bindEvents();
    this.controls.addEventListener("change", () => this.wake());
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
    this.invalidate();
  }

  bindEvents() {
    const el = this.renderer.domElement;
    el.addEventListener("pointerdown", (e) => { this.pointerDown = [e.clientX, e.clientY]; });
    el.addEventListener("pointerup", (e) => {
      if (this.viewHelper.handleClick(e)) { this.wake(); return; }
      const d = this.pointerDown;
      this.pointerDown = null;
      if (!d || Math.hypot(e.clientX - d[0], e.clientY - d[1]) > 4) return;
      const group = this.pick(e);
      this.select(group);
      if (this.onPick) this.onPick(group >= 0 ? this.groups[group] : null, group);
    });
    el.addEventListener("pointermove", (e) => {
      if (e.buttons) return;
      const group = this.pick(e);
      if (group !== this.hovered) {
        this.hovered = group;
        el.style.cursor = group >= 0 ? "pointer" : "";
        this.paint();
      }
    });
    el.addEventListener("pointerleave", () => { this.hovered = -1; this.paint(); });
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.invalidate();
  }

  // Render on demand: draw at once when something changes, and keep a frame loop only
  // while the camera is moving (damping, gizmo animation). Nothing is drawn when idle.
  draw() {
    this.renderer.clear();
    this.renderer.render(this.scene, this.camera);
    this.viewHelper.render(this.renderer);
  }

  invalidate() {
    this.draw();
    this.wake();
  }

  wake() {
    if (this.looping) return;
    this.looping = true;
    this.clock.getDelta();
    requestAnimationFrame(() => this.loop());
  }

  loop() {
    const delta = this.clock.getDelta();
    let moving = false;
    if (this.viewHelper.animating) {
      this.viewHelper.update(delta);
      moving = true;
    }
    if (this.controls.update()) moving = true;
    this.draw();
    if (moving) requestAnimationFrame(() => this.loop());
    else this.looping = false;
  }

  // ---------- content ----------

  clear() {
    for (const child of [...this.root.children]) {
      child.traverse((o) => {
        o.geometry?.dispose();
        (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m?.dispose());
      });
      this.root.remove(child);
    }
    this.mesh = null;
    this.groups = [];
    this.ranges = [];
    this.selected = this.hovered = -1;
    this.annotations = new Map();
    this.marked = new Set();
  }

  show(result, { fit = false } = {}) {
    this.lastResult = result;
    const hidden = this.hiddenAnnotations || new Set();
    this.clear();
    if (result.dim === 2) this.show2d(result);
    else this.show3d(result);
    const drawn = this.showAnnotations(result.annotations || []);
    for (const id of hidden) this.setAnnotationVisible(id, false);
    this.stage(unionBounds(result.bounds, drawn));
    if (fit) this.setView(this.is2d ? "top" : "iso");
    this.invalidate();
  }

  // ---------- annotations (FR-C11): lines and filled glyphs in model coordinates ----------

  showAnnotations(list) {
    const ink = annotationInk();
    let lo = null, hi = null;
    const grow = (a) => {
      for (let i = 0; i < a.length; i += 3) {
        const p = [a[i], a[i + 1], a[i + 2]];
        lo = lo ? lo.map((v, k) => Math.min(v, p[k])) : p.slice();
        hi = hi ? hi.map((v, k) => Math.max(v, p[k])) : p.slice();
      }
    };
    for (const a of list) {
      if (!a.lines && !a.triangles) continue;
      const color = annotationColor(a.color, ink);
      const group = new THREE.Group();
      group.userData.color = color;
      if (a.lines) {
        const segments = [];
        for (const line of a.lines) {
          grow(line);
          for (let i = 0; i + 5 < line.length; i += 3) segments.push(...line.slice(i, i + 6));
        }
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.Float32BufferAttribute(segments, 3));
        group.add(new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color })));
      }
      if (a.triangles) {
        grow(a.triangles);
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.Float32BufferAttribute(a.triangles, 3));
        group.add(new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide })));
      }
      group.renderOrder = 2;
      this.annotations.set(a.id, group);
      this.root.add(group);
    }
    return lo ? [lo, hi] : null;
  }

  setAnnotationVisible(id, visible) {
    this.hiddenAnnotations = this.hiddenAnnotations || new Set();
    if (visible) this.hiddenAnnotations.delete(id);
    else this.hiddenAnnotations.add(id);
    const group = this.annotations.get(id);
    if (group) group.visible = visible;
    this.invalidate();
  }

  // Emphasise some annotations (accent) and the faces whose source entity is in `entities`.
  emphasise(ids = [], entities = []) {
    const accent = themeColors().accent;
    const on = new Set(ids);
    for (const [id, group] of this.annotations) {
      for (const child of group.children) child.material.color.copy(on.has(id) ? accent : group.userData.color);
    }
    this.marked = new Set(this.groups.map((g, i) => (g.entity && entities.includes(g.entity) ? i : -1)).filter((i) => i >= 0));
    this.paint();
  }

  // A saved view: look along its direction with its up, framing the model.
  lookAlong(camera) {
    if (!this.bounds || !camera) return;
    const [, dir, up] = camera.map((v) => new THREE.Vector3(...v));
    const distance = this.fitDistance();
    this.controls.target.copy(this.bounds.center);
    this.camera.position.copy(this.bounds.center).addScaledVector(dir.normalize(), -distance);
    this.camera.up.copy(up.normalize());
    this.camera.lookAt(this.bounds.center);
    this.controls.update();
    this.invalidate();
  }

  show3d(result) {
    this.is2d = false;
    const positions = decode(result.positions, Float32Array);
    const indices = decode(result.indices, Uint32Array);
    const triGroup = decode(result.triGroup, Uint32Array);
    this.groups = result.groups;

    // Sort triangles by group so each group is one draw range with its own material.
    const n = triGroup.length;
    const order = Array.from({ length: n }, (_, i) => i).sort((a, b) => triGroup[a] - triGroup[b] || a - b);
    const sorted = new Uint32Array(n * 3);
    order.forEach((t, i) => sorted.set(indices.subarray(t * 3, t * 3 + 3), i * 3));
    let geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setIndex(new THREE.BufferAttribute(sorted, 1));
    const edgesSource = geometry.clone();
    geometry = toCreasedNormals(geometry, THREE.MathUtils.degToRad(32));

    this.ranges = [];
    const materials = this.groups.map((g) => {
      const color = g.color ? new THREE.Color().setRGB(g.color[0], g.color[1], g.color[2], THREE.SRGBColorSpace) : DEFAULT_COLOR.clone();
      const opacity = g.color ? g.color[3] : 1;
      return new THREE.MeshStandardMaterial({
        color, metalness: 0.08, roughness: 0.48, transparent: opacity < 1, opacity,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
        wireframe: this.options.wireframe,
      });
    });
    let start = 0;
    for (let g = 0; g < this.groups.length; g++) {
      let count = 0;
      while (start + count < n && triGroup[order[start + count]] === g) count++;
      if (count) geometry.addGroup(start * 3, count * 3, g);
      this.ranges.push([start, start + count]);
      start += count;
    }
    const mesh = new THREE.Mesh(geometry, materials);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    this.mesh = mesh;
    this.root.add(mesh);

    const colors = themeColors();
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(edgesSource, 28),
      new THREE.LineBasicMaterial({ color: colors.edge, transparent: true, opacity: colors.dark ? 0.75 : 0.55 }),
    );
    edges.visible = this.options.edges && !this.options.wireframe;
    this.edges = edges;
    this.root.add(edges);
    edgesSource.dispose();
  }

  show2d(result) {
    this.is2d = true;
    const colors = themeColors();
    const contours = result.contours.map((c) => c.map(([x, y]) => new THREE.Vector2(x, y)));
    const outers = contours.filter((c) => !THREE.ShapeUtils.isClockWise(c));
    const holes = contours.filter((c) => THREE.ShapeUtils.isClockWise(c));
    const inside = (p, poly) => {
      let hit = false;
      for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
        const a = poly[i], b = poly[j];
        if ((a.y > p.y) !== (b.y > p.y) && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) hit = !hit;
      }
      return hit;
    };
    const shapes = outers.map((outer) => {
      const shape = new THREE.Shape(outer);
      shape.holes = holes.filter((h) => inside(h[0], outer)).map((h) => new THREE.Path(h));
      return shape;
    });
    const fill = new THREE.Mesh(
      new THREE.ShapeGeometry(shapes),
      new THREE.MeshBasicMaterial({ color: colors.accent, transparent: true, opacity: colors.dark ? 0.22 : 0.16, side: THREE.DoubleSide }),
    );
    this.root.add(fill);
    const lineMaterial = new THREE.LineBasicMaterial({ color: colors.accent });
    for (const c of contours) {
      const points = [...c, c[0]].map((p) => new THREE.Vector3(p.x, p.y, 0.001));
      this.root.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), lineMaterial));
    }
    this.groups = [];
  }

  stage(bounds) {
    if (!bounds) return;
    const lo = new THREE.Vector3(...bounds[0]);
    const hi = new THREE.Vector3(...bounds[1]);
    const size = hi.clone().sub(lo);
    const center = lo.clone().add(hi).multiplyScalar(0.5);
    const span = Math.max(size.x, size.y, size.z, 1);
    this.bounds = { lo, hi, center, span };

    // Grid: a round step a little smaller than the model, extending well past it.
    if (this.grid) { this.grid.geometry.dispose(); this.grid.material.dispose(); this.scene.remove(this.grid); }
    const step = 10 ** Math.floor(Math.log10(span / 2));
    const extent = Math.ceil((span * 3) / step) * step;
    const colors = themeColors();
    this.grid = new THREE.GridHelper(extent * 2, Math.round((extent * 2) / step), colors.grid, colors.grid2);
    this.grid.rotation.x = Math.PI / 2;
    this.grid.position.set(Math.round(center.x / step) * step, Math.round(center.y / step) * step, this.is2d ? -0.01 : lo.z);
    this.grid.material.transparent = true;
    this.grid.material.opacity = colors.dark ? 0.55 : 0.8;
    this.grid.visible = this.options.grid;
    this.scene.add(this.grid);

    this.shadowFloor.visible = !this.is2d;
    this.shadowFloor.scale.set(span * 8, span * 8, 1);
    this.shadowFloor.position.set(center.x, center.y, lo.z - span * 0.001);

    this.key.position.set(center.x + span * 1.2, center.y - span * 0.8, center.z + span * 2.2);
    this.key.target.position.copy(center);
    const cam = this.key.shadow.camera;
    cam.left = cam.bottom = -span * 1.5;
    cam.right = cam.top = span * 1.5;
    cam.near = 0.1;
    cam.far = span * 8;
    cam.updateProjectionMatrix();
    this.rim.position.set(center.x - span * 2, center.y + span * 2, center.z + span);
    this.camera.near = span / 500;
    this.camera.far = span * 200;
    this.camera.updateProjectionMatrix();
  }

  fitDistance() {
    const vfov = THREE.MathUtils.degToRad(this.camera.fov);
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * this.camera.aspect);
    if (this.is2d) {
      // A flat drawing seen from above: fit its rectangle, not its bounding sphere.
      const size = this.bounds.hi.clone().sub(this.bounds.lo);
      return Math.max(size.x / 2 / Math.tan(hfov / 2), size.y / 2 / Math.tan(vfov / 2), 1) * 1.3;
    }
    // Frame the bounding sphere with breathing room on the narrower side of the view.
    const radius = this.bounds.lo.distanceTo(this.bounds.hi) / 2 || 1;
    return (radius / Math.sin(Math.min(vfov, hfov) / 2)) * 1.25;
  }

  setView(name) {
    if (!this.bounds) return;
    const dir = new THREE.Vector3(...(VIEWS[name] || VIEWS.iso)).normalize();
    const distance = this.fitDistance();
    this.controls.target.copy(this.bounds.center);
    this.camera.position.copy(this.bounds.center).addScaledVector(dir, distance);
    this.camera.up.set(0, name === "top" ? 1 : 0, name === "top" ? 0 : 1);
    this.camera.lookAt(this.bounds.center);
    this.camera.up.set(0, 0, 1);
    this.offsetFraming(distance);
    this.controls.update();
    this.invalidate();
  }

  // Nudge a 3D model up and right on screen, clear of the stats card in the lower left.
  offsetFraming(distance) {
    if (this.is2d) return;
    const half = distance * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2);
    this.camera.updateMatrixWorld();
    const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0);
    const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 1);
    const shift = right.multiplyScalar(-0.1 * half * this.camera.aspect).add(up.multiplyScalar(-0.06 * half));
    this.camera.position.add(shift);
    this.controls.target.add(shift);
  }

  fit() {
    if (!this.bounds) return;
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    const distance = this.fitDistance();
    this.controls.target.copy(this.bounds.center);
    this.camera.position.copy(this.bounds.center).addScaledVector(dir, distance);
    this.offsetFraming(distance);
    this.controls.update();
    this.invalidate();
  }

  set(option, value) {
    this.options[option] = value;
    if (this.grid) this.grid.visible = this.options.grid;
    if (this.edges) this.edges.visible = this.options.edges && !this.options.wireframe;
    if (this.mesh) for (const m of this.mesh.material) m.wireframe = this.options.wireframe;
    this.invalidate();
  }

  refreshTheme() {
    if (this.lastResult) this.show(this.lastResult);
  }

  // ---------- picking ----------

  pick(event) {
    if (!this.mesh) return -1;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(ndc, this.camera);
    const hit = this.raycaster.intersectObject(this.mesh, false)[0];
    if (!hit) return -1;
    const tri = hit.faceIndex;
    return this.ranges.findIndex(([a, b]) => tri >= a && tri < b);
  }

  select(group) {
    this.selected = group;
    this.paint();
  }

  paint() {
    if (!this.mesh) return;
    const accent = themeColors().accent;
    this.mesh.material.forEach((m, i) => {
      const strength = i === this.selected ? 0.38 : this.marked?.has(i) ? 0.3 : i === this.hovered ? 0.16 : 0;
      m.emissive.copy(accent).multiplyScalar(strength);
    });
    this.invalidate();
  }

  snapshot() {
    return this.renderer.domElement.toDataURL("image/png");
  }
}
