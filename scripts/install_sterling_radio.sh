#!/usr/bin/env bash
# Install ONLY publishable static Radio assets. Never publish the source repo,
# secrets, .env files, databases, node_modules or Vite/React source files.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/sterling-radio"
DEST="$ROOT/frontend/public/radio"
[[ -d "$SRC" ]] || { echo "ERROR: Missing $SRC"; exit 1; }
if [[ -f "$SRC/dist/index.html" ]]; then
  PUBLIC="$SRC/dist"; ENTRY="index.html"
elif [[ -f "$SRC/build/index.html" ]]; then
  PUBLIC="$SRC/build"; ENTRY="index.html"
elif [[ -f "$SRC/index.html" ]]; then
  PUBLIC="$SRC"; ENTRY="index.html"
elif [[ -f "$SRC/sterling-radio.html" ]]; then
  PUBLIC="$SRC"; ENTRY="sterling-radio.html"
elif [[ -f "$SRC/radio.html" ]]; then
  PUBLIC="$SRC"; ENTRY="radio.html"
else
  echo "ERROR: No Radio HTML entry in sterling-radio/."
  echo "If this is a React/Vite project, build it first (npm run build)."
  find "$SRC" -maxdepth 2 -type f \( -iname '*.html' -o -name package.json \) -print
  exit 2
fi

# Source-tree Vite index.html requires transpilation before public deployment.
if [[ "$PUBLIC" == "$SRC" && -f "$SRC/package.json" ]] && grep -Eq 'src=["\x27]/src/|type=["\x27]module["\x27][^>]*src=["\x27]/src/' "$PUBLIC/$ENTRY"; then
  echo "ERROR: Radio is an unbuilt Vite project; run its build and rerun this installer."
  exit 2
fi

python3 - "$PUBLIC" "$ENTRY" "$DEST" "$HOME/.local/share/sterling-radio-backups" <<'PY'
import pathlib, shutil, sys, re, time
source, entry, dest, backup_root = map(pathlib.Path, sys.argv[1:])
if not source.is_dir() or not (source / entry).is_file():
    raise SystemExit("Radio input is missing")
allowed = {".html", ".htm", ".css", ".js", ".mjs", ".svg", ".png",
           ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2",
           ".ttf", ".otf", ".eot", ".webmanifest", ".mp3", ".ogg",
           ".wav", ".aac", ".mp4", ".webm", ".avif", ".json"}
blocked = re.compile(r"(^|[/_.-])(\.env|secret|credential|private|\.git|node_modules|token|password|\.pem|\.key)([/.\-_]|$)", re.I)
paths = []
for item in source.rglob("*"):
    if not item.is_file() or item.is_symlink():
        continue
    rel = item.relative_to(source)
    if any(part.startswith(".") for part in rel.parts) or blocked.search(str(rel)):
        continue
    if item.suffix.lower() not in allowed:
        continue
    if item.stat().st_size > 50_000_000:
        raise SystemExit(f"Unexpectedly large Radio file: {rel}")
    paths.append((item,rel))
if not any(str(rel) == entry for _,rel in paths):
    raise SystemExit("Radio entry excluded by asset safety filter")
for item,rel in paths:
    if item.suffix.lower() in {".html",".htm",".js",".mjs",".json"} and item.stat().st_size < 5_000_000:
        content=item.read_text(encoding="utf-8",errors="replace")
        if re.search(r"-----BEGIN (?:OPENSSH|RSA|EC|DSA|PRIVATE) PRIVATE KEY-----",content):
            raise SystemExit(f"Refusing private key in public Radio asset: {rel}")
if dest.exists() and any(dest.iterdir()):
    backup_root.mkdir(parents=True,exist_ok=True)
    backup = backup_root / f"radio-backup-{time.strftime('%Y%m%d-%H%M%S')}"
    if backup.exists():
        raise SystemExit("Radio backup path collision; retry")
    dest.rename(backup)
    print(f"Prior public Radio assets preserved: {backup}")
dest.mkdir(parents=True,exist_ok=True)
for item,rel in paths:
    target=dest/rel
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(item,target)
if entry != "index.html":
    shutil.copy2(dest/entry,dest/"index.html")
html = (dest/"index.html").read_text(encoding="utf-8",errors="replace")
if re.search(r"""(?:src|href)=['"]/(?:assets|src)/""",html):
    print("WARNING: Radio HTML has root-absolute assets. Build with base=/radio/ if missing.")
print(f"Installed {len(paths)} static Radio files in {dest}")
print("IMPORTANT: Validate Radio playback, mobile layout and browser console.")
PY
