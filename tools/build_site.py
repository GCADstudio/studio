"""Build the web studio, a static site (SPEC §4.1, FR-W7, FR-W8).

    python tools/build_site.py [--out site] [--cache .cache/site-assets]

The site is src/gcad/web with the transport switched to the in-browser worker, a gcad wheel
built from this checkout, and the third-party files pinned in tools/site-assets.toml. Every
download is checked against its SHA-256 and kept in the cache, so a second build needs no
network. The same checkout and manifest give the same bytes. Serve the folder with any static
file server, for example `python -m http.server -d site`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import shutil
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "src" / "gcad"
MANIFEST = Path(__file__).resolve().parent / "site-assets.toml"
ZIP_DATE = (1980, 1, 1, 0, 0, 0)  # the earliest a zip can say; fixed for reproducibility


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, digest: str, cache: Path) -> bytes:
    """The file at `url`, from the cache when there, refused unless its SHA-256 is `digest`."""
    cached = cache / digest
    if cached.is_file():
        data = cached.read_bytes()
        if sha256(data) == digest:
            return data
        print(f"  cached copy of {url} is damaged; fetching it again")
    print(f"  fetching {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "gcad-build-site"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    actual = sha256(data)
    if actual != digest:
        sys.exit(f"error: {url} has SHA-256 {actual}, expected {digest}; refusing to use it")
    cache.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(data)
    return data


def npm_files(tgz: bytes, names: list[str]) -> dict[str, bytes]:
    """Files from the `package/` folder of an npm tarball."""
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tar:
        for name in names:
            member = tar.extractfile(f"package/{name}")
            if member is None:
                sys.exit(f"error: package/{name} is missing from the npm tarball")
            out[name] = member.read()
    return out


def lock_closure(lock: dict, wanted: list[str]) -> list[str]:
    """`wanted` and every package they depend on, from Pyodide's lock file."""
    seen: list[str] = []
    stack = list(wanted)
    while stack:
        name = stack.pop(0)
        if name in seen:
            continue
        if name not in lock["packages"]:
            sys.exit(f"error: Pyodide's lock file has no package '{name}'")
        seen.append(name)
        stack.extend(lock["packages"][name].get("depends", []))
    return seen


def write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, ZIP_DATE)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[name])


def gcad_wheel(out: Path) -> str:
    """A pure-Python wheel of this checkout's gcad package, without the web UI (the site
    serves that itself). Written directly, so the build needs no packaging tools."""
    found = re.search(r'__version__ = "([^"]+)"', (PACKAGE / "__init__.py").read_text())
    if found is None:
        sys.exit("error: no __version__ in src/gcad/__init__.py")
    version = found.group(1)
    dist = f"gcad_studio-{version}"
    files: dict[str, bytes] = {}
    for path in sorted(PACKAGE.rglob("*")):
        rel = path.relative_to(PACKAGE.parent)
        if path.is_dir() or "__pycache__" in rel.parts or rel.parts[1:2] == ("web",):
            continue
        if path.suffix in (".pyc", ".pyo"):
            continue
        files[rel.as_posix()] = path.read_bytes()
    files[f"{dist}.dist-info/METADATA"] = (
        f"Metadata-Version: 2.1\nName: gcad-studio\nVersion: {version}\n"
        "Summary: GCAD Studio core, built for the web studio (Pyodide)\n"
        "License: AGPL-3.0-or-later\n"
    ).encode()
    files[f"{dist}.dist-info/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: gcad build_site\nRoot-Is-Purelib: true\n"
        b"Tag: py3-none-any\n"
    )
    files[f"{dist}.dist-info/LICENSE"] = (REPO / "LICENSE").read_bytes()

    def record_hash(data: bytes) -> str:
        return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()

    record = [f"{n},sha256={record_hash(d)},{len(d)}" for n, d in sorted(files.items())]
    record.append(f"{dist}.dist-info/RECORD,,")
    files[f"{dist}.dist-info/RECORD"] = ("\n".join(record) + "\n").encode()
    name = f"{dist}-py3-none-any.whl"
    write_zip(out / name, files)
    return name


def build(out: Path, cache: Path) -> None:
    assets = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    if out.exists():
        if any(out.iterdir()) and not (out / "site.json").is_file():
            sys.exit(f"error: {out} is not empty and not a built site; refusing to replace it")
        shutil.rmtree(out)
    shutil.copytree(PACKAGE / "web", out, ignore=shutil.ignore_patterns("__pycache__"))
    (out / "js" / "mode.js").write_text(
        "// Written by tools/build_site.py: this is the web studio (SPEC §4.1).\n"
        'export const MODE = "web";\n',
        encoding="utf-8",
    )
    (out / ".nojekyll").write_bytes(b"")  # GitHub Pages: serve the files as they are

    print(f"Pyodide {assets['pyodide']['version']}")
    py = assets["pyodide"]
    runtime = npm_files(fetch(py["url"], py["sha256"], cache), py["files"])
    (out / "pyodide").mkdir()
    for name, data in runtime.items():
        (out / "pyodide" / name).write_bytes(data)
    lock = json.loads(runtime["pyodide-lock.json"])
    packages = lock_closure(lock, py["packages"])
    for name in packages:
        entry = lock["packages"][name]
        data = fetch(py["packages_url"] + entry["file_name"], entry["sha256"], cache)
        (out / "pyodide" / entry["file_name"]).write_bytes(data)

    print(f"manifold-3d {assets['manifold']['version']}")
    mf = assets["manifold"]
    (out / "manifold").mkdir()
    for name, data in npm_files(fetch(mf["url"], mf["sha256"], cache), mf["files"]).items():
        (out / "manifold" / name).write_bytes(data)

    oc = assets["occt"]
    print(f"occt-import-js {oc['version']}")
    (out / "occt").mkdir()
    for name, data in npm_files(fetch(oc["url"], oc["sha256"], cache), oc["files"]).items():
        (out / "occt" / Path(name).name).write_bytes(data)

    (out / "wheels").mkdir()
    wheels: list[str] = []
    for wheel in assets["wheels"]:
        print(f"{wheel['name']} {wheel['version']}")
        name = wheel["url"].rsplit("/", 1)[1]
        (out / "wheels" / name).write_bytes(fetch(wheel["url"], wheel["sha256"], cache))
        wheels.append(f"wheels/{name}")
    gcad = gcad_wheel(out / "wheels")
    wheels.append(f"wheels/{gcad}")
    print(gcad)

    site = {"pyodide": py["version"], "pyodidePackages": packages, "wheels": wheels}
    (out / "site.json").write_text(json.dumps(site, indent=2) + "\n", encoding="utf-8")
    notices = [
        "GCAD Studio, web edition. GCAD Studio is AGPL-3.0-or-later:",
        "https://github.com/GCADstudio/studio",
        "",
        "Third-party software served with this site (versions pinned in tools/site-assets.toml):",
        f"- Pyodide {py['version']} ({py['license']}), with CPython: {py['source']}",
        *(
            f"- {name} {lock['packages'][name]['version']} (Pyodide package): {py['source']}"
            for name in packages
        ),
        f"- manifold-3d {mf['version']} ({mf['license']}): {mf['source']}; see manifold/LICENSE",
        f"- occt-import-js {oc['version']} with OpenCASCADE ({oc['license']}): {oc['source']}; "
        "see occt/license.occt-import-js.txt and occt/license.occt.txt",
        *(f"- {w['name']} {w['version']} ({w['license']})" for w in assets["wheels"]),
        "- three.js and CodeMirror (MIT): see vendor/",
        "",
    ]
    (out / "THIRD-PARTY.txt").write_text("\n".join(notices), encoding="utf-8")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"built {out} ({size / 1e6:.1f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=REPO / "site", help="output folder")
    parser.add_argument(
        "--cache", type=Path, default=REPO / ".cache" / "site-assets", help="download cache"
    )
    args = parser.parse_args()
    build(args.out.resolve(), args.cache.resolve())


if __name__ == "__main__":
    main()
