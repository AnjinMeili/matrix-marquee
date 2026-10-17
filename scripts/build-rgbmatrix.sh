#!/bin/sh
# Build the rpi-rgb-led-matrix Python bindings into a cached wheel.
#
# Run this on the Pi (as root) once per OS/Python/Pillow change. Each protest
# release creates a fresh virtualenv, so install.sh reinstalls the wheel from
# /opt/protest/wheels rather than rebuilding the C++ library every deploy.
set -eu

REPO_URL="https://github.com/hzeller/rpi-rgb-led-matrix.git"
REPO_DIR="${REPO_DIR:-/opt/rpi-rgb-led-matrix}"
WHEEL_DIR="${WHEEL_DIR:-/opt/protest/wheels}"
INCLUDE_DIR="${INCLUDE_DIR:-/opt/protest/build-include}"
APP_PYTHON="${APP_PYTHON:-/opt/protest/current/.venv/bin/python}"

if [ "$(id -u)" -ne 0 ]; then
    echo "error: must run as root" >&2
    exit 1
fi

apt-get update
apt-get install -y --no-install-recommends \
    git build-essential python3-dev python3-venv cython3 curl

if [ -d "$REPO_DIR/.git" ]; then
    git -C "$REPO_DIR" fetch --depth 1 origin
    git -C "$REPO_DIR" reset --hard origin/HEAD
else
    git clone --depth 1 "$REPO_URL" "$REPO_DIR"
fi

# Build the native library and the C++ examples (useful for hardware triage).
make -C "$REPO_DIR/lib"
make -C "$REPO_DIR/examples-api-use"

# The bindings compile shims/pillow.c, which includes Pillow's private
# "Imaging.h". Upstream never adds that include path and Pillow's binary wheels
# do not ship C headers, so the build fails with:
#   fatal error: Imaging.h: No such file or directory
# The shim pokes at ImagingMemoryInstance directly, so the headers MUST match
# the Pillow version the application actually imports at runtime.
pillow_version=$("$APP_PYTHON" -c 'import PIL; print(PIL.__version__)')
echo "using Pillow headers for runtime version ${pillow_version}"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

sdist_url=$(curl -fsSL "https://pypi.org/pypi/pillow/${pillow_version}/json" |
    python3 -c 'import json,sys; print(next(u["url"] for u in json.load(sys.stdin)["urls"] if u["packagetype"]=="sdist"))')
curl -fsSL -o "$work/pillow.tar.gz" "$sdist_url"
tar xzf "$work/pillow.tar.gz" -C "$work"

install -d -m 0755 "$INCLUDE_DIR"
rm -rf "$INCLUDE_DIR/libImaging"
cp -r "$work/pillow-${pillow_version}/src/libImaging" "$INCLUDE_DIR/libImaging"

install -d -m 0755 "$WHEEL_DIR"
rm -f "$WHEEL_DIR"/rgbmatrix-*.whl

python3 -m venv "$work/buildenv"
"$work/buildenv/bin/pip" install --disable-pip-version-check --no-input -q build wheel

(
    cd "$REPO_DIR"
    "$work/buildenv/bin/pip" wheel \
        --disable-pip-version-check --no-input --no-deps \
        --wheel-dir "$WHEEL_DIR" \
        --config-settings=cmake.define.CMAKE_C_FLAGS="-I${INCLUDE_DIR}/libImaging" \
        .
)

echo "built: $(ls "$WHEEL_DIR"/rgbmatrix-*.whl)"
