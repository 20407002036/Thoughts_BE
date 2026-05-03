#!/usr/bin/env bash
set -euo pipefail

# Set writable locations for Rust toolchain to avoid read-only filesystem errors
export CARGO_HOME=${CARGO_HOME:-/tmp/.cargo}
export RUSTUP_HOME=${RUSTUP_HOME:-/tmp/.rustup}
export PATH="$CARGO_HOME/bin:$PATH"

echo "Installing rustup to ${RUSTUP_HOME} and cargo to ${CARGO_HOME} (non-interactive)..."
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
source "$CARGO_HOME/env" || true

echo "Setting default Rust toolchain to stable..."
rustup default stable || true

echo "Upgrading pip tooling and installing Python dependencies..."
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

echo "Build script finished."
