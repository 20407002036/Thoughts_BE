#!/usr/bin/env bash
# set -euo pipefail

# # Set writable Cargo cache location to avoid read-only filesystem errors.
# # Render/hosts already have Rust installed; we just need a writable cache directory.
# export CARGO_HOME=/tmp/.cargo
# mkdir -p "$CARGO_HOME"

# # Allow PyO3 to work with Python 3.13+ using stable ABI (forward compatibility).
# # This suppresses the version check when pydantic-core is built.
# export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1

# echo "Using Cargo home at ${CARGO_HOME}"
# # echo "PyO3 ABI3 forward compatibility enabled"
# echo "Upgrading pip tooling and installing Python dependencies..."
# python -m pip install --upgrade pip setuptools wheel
python -m venv venv && source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "Build script finished."
