#!/usr/bin/env bash
set -euo pipefail

# Set writable Cargo cache location to avoid read-only filesystem errors.
# Render/hosts already have Rust installed; we just need a writable cache directory.
export CARGO_HOME=/tmp/.cargo
mkdir -p "$CARGO_HOME"

echo "Using Cargo home at ${CARGO_HOME}"
echo "Upgrading pip tooling and installing Python dependencies..."
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

echo "Build script finished."
