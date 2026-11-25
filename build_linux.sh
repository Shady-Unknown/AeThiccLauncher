#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="AeThiccLauncher"
FLATPAK_DIR="aethicc-flatpak"
MANIFEST="$FLATPAK_DIR/com.MJK.AeThiccLauncher.yml"
OUTPUT="Ae_Thicc_Launcher_Linux.flatpak"

# Container image for PyInstaller — must have an older glibc than the Flatpak runtime
BUILD_IMAGE="python:3.12-slim-bookworm"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

STEP_NUM=0
STEP_TOTAL=8

header() {
    STEP_NUM=$((STEP_NUM + 1))
    echo ""
    echo -e "${YELLOW}─── Step ${STEP_NUM}/${STEP_TOTAL}: $* ───${NC}"
}

info()  { echo -e "${CYAN}  ▸${NC} $*"; }
ok()    { echo -e "${GREEN}  ✓${NC} $* $(date +%H:%M:%S)"; }
warn()  { echo -e "${YELLOW}  ⚠${NC} $*"; }
err()   { echo -e "${RED}  ✗${NC} $*"; exit 1; }

SECONDS=0
elapsed() { echo -e "${DIM}    ($SECONDS seconds)${NC}"; }

# ────────────────────────────────────────────────────────────────────
# Step 1 — Local System Dependencies (flatpak-builder + deps)
# ────────────────────────────────────────────────────────────────────
header "Local System Dependencies (flatpak-builder, etc.)"
SECONDS=0
if command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y git flatpak flatpak-builder
elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y git flatpak flatpak-builder
else
    err "Install git, flatpak and flatpak-builder manually"
fi
ok "Local deps ready"
elapsed

# ────────────────────────────────────────────────────────────────────
# Step 2 — Ensure Flathub Remote
# ────────────────────────────────────────────────────────────────────
header "Flatpak Setup"
SECONDS=0
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
ok "Flathub remote ready"
elapsed

# ────────────────────────────────────────────────────────────────────
# Step 3 — Build Binary Inside Container (glibc compat)
# ────────────────────────────────────────────────────────────────────
header "Compile Binary in $BUILD_IMAGE (glibc compat)"
SECONDS=0

# Pull the image if not present
info "Pulling $BUILD_IMAGE..."
podman pull --quiet "$BUILD_IMAGE"

info "Building binary inside container (this takes a while)..."
echo ""

SOURCE_DATE_EPOCH="$(git log -1 --format=%ct 2>/dev/null || echo 0)"
info "SOURCE_DATE_EPOCH: $SOURCE_DATE_EPOCH"

podman run --rm \
    -e SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" \
    -e AETHICC_BUILD_MODE=onefile \
    -e AETHICC_APP_NAME="$APP_NAME" \
    -v "$SCRIPT_DIR":/work \
    -w /work \
    "$BUILD_IMAGE" \
    bash -c '
        set -euo pipefail

        echo "── Installing system deps ──"
        apt-get update
        apt-get install -y --no-install-recommends \
            git libgl1 libegl1 libnss3 libxkbcommon-x11-0 libdbus-1-3 \
            libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
            libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 binutils

        echo "── Installing Python packages ──"
        python -m pip install --upgrade pip
        python -m pip install -r /work/requirements-build.txt

        echo "── Cleaning previous builds ──"
        rm -rf /work/dist /work/build

        echo "── Compiling with PyInstaller ──"
        python -m PyInstaller --clean --noconfirm Ae_Thicc_Launcher.spec

        echo "── Verifying binary ──"
        ls -lh "/work/dist/$AETHICC_APP_NAME"
        echo "DONE"
    '

echo ""
[ -f "dist/$APP_NAME" ] || err "PyInstaller failed — dist/$APP_NAME not found"
ok "Binary compiled: dist/$APP_NAME ($(du -h "dist/$APP_NAME" | cut -f1))"
elapsed

# ────────────────────────────────────────────────────────────────────
# Step 4 — Copy Assets into Flatpak Dir
# ────────────────────────────────────────────────────────────────────
header "Copy Assets into $FLATPAK_DIR/"
SECONDS=0
cp "dist/$APP_NAME" "$FLATPAK_DIR/$APP_NAME"
info "Copied $APP_NAME"
# `cp -r src dest` nests into an existing dest: wipe first so a stale .media
# (missing icons/, new fonts) can never be shipped.
rm -rf "$FLATPAK_DIR/.media"
cp -r .media "$FLATPAK_DIR/.media"
info "Copied .media/"
rm -rf "$FLATPAK_DIR/bin"
cp -r bin   "$FLATPAK_DIR/bin"
info "Copied bin/"
if [ -d data ]; then
    rm -rf "$FLATPAK_DIR/data"
    cp -r data "$FLATPAK_DIR/data"
    info "Copied data/ (shipped definitions and indexes)"
fi
if [ -d lists ]; then
    rm -rf "$FLATPAK_DIR/lists"
    cp -r lists "$FLATPAK_DIR/lists"
    info "Copied lists/ (gold tier seeds)"
fi
ok "Assets ready"
elapsed

# ────────────────────────────────────────────────────────────────────
# Step 5 — Build Flatpak (flatpak-builder)
# ────────────────────────────────────────────────────────────────────
header "Build Flatpak (flatpak-builder)"
SECONDS=0
info "Building (this may take a while on first run)..."
echo ""
flatpak-builder \
    --repo=repo \
    build-dir \
    "$MANIFEST" \
    --force-clean \
    --install-deps-from=flathub
echo ""
ok "Flatpak built"
elapsed

# ────────────────────────────────────────────────────────────────────
# Step 6 — Bundle
# ────────────────────────────────────────────────────────────────────
header "Bundle Flatpak"
SECONDS=0
info "Creating $OUTPUT..."
flatpak build-bundle repo "$OUTPUT" com.MJK.AeThiccLauncher
[ -f "$OUTPUT" ] || err "Flatpak bundle failed — $OUTPUT not found"
ok "Output: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
python3 verify_release.py --platform linux --artifacts-dir . --generate-sums --require-sums
ok "Checksum: SHA256SUMS.txt"
elapsed

# ────────────────────────────────────────────────────────────────────
# Step 7 — Cleanup Build Artifacts
# ────────────────────────────────────────────────────────────────────
header "Cleanup Build Artifacts"
SECONDS=0
info "Removing PyInstaller intermediates..."
rm -rf dist build
info "Removing copied assets from $FLATPAK_DIR/..."
rm -f  "$FLATPAK_DIR/$APP_NAME"
rm -rf "$FLATPAK_DIR/.media" "$FLATPAK_DIR/bin" "$FLATPAK_DIR/data" "$FLATPAK_DIR/lists"
info "Removing flatpak-builder cache..."
rm -rf "$FLATPAK_DIR/repo" "$FLATPAK_DIR/build-dir" "$FLATPAK_DIR/.flatpak-builder"
rm -rf repo
info "Removing .venv..."
rm -rf .venv
ok "Cleanup done — only $OUTPUT remains"
elapsed

echo ""
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  BUILD COMPLETE${NC}"
echo -e "${GREEN}  $SCRIPT_DIR/$OUTPUT${NC}"
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
