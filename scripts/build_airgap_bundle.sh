#!/usr/bin/env bash
# Build a self-contained offline bundle for an air-gapped Linux/macOS machine.
#
# Run this on an INTERNET-CONNECTED box with the SAME OS/arch/Python version as
# the target air-gapped machine. Produces ./airgap_bundle/ (+ a .tar.gz) with:
#   * the trace32-mcp wheel (ONNX model + manuals DB baked in)
#   * a wheelhouse of every runtime dependency as wheels
#   * install_offline.sh for the offline machine
#
#   bash scripts/build_airgap_bundle.sh
#
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
out="$repo/airgap_bundle"
wheelhouse="$out/wheelhouse"
rm -rf "$out"; mkdir -p "$wheelhouse"

# 1. Ensure the ONNX model is vendored (needs the [build] extra: torch etc.)
if [ ! -f src/trace32_mcp/model/model.onnx ]; then
  echo "[bundle] ONNX model missing — exporting it (one-time, needs internet + torch)..."
  python -m pip install -e ".[build]"
  python scripts/build_onnx_model.py
fi

# 2. Build the wheel (model + DB baked in via package-data)
echo "[bundle] building the trace32-mcp wheel..."
python -m pip install -q --upgrade build
python -m build --wheel --outdir "$out"
wheel="$(ls "$out"/trace32_mcp-*.whl | head -1)"

# 3. Download all RUNTIME deps for this platform into the wheelhouse
echo "[bundle] downloading runtime dependencies into the wheelhouse..."
python -m pip download "$wheel" --dest "$wheelhouse"
python -m pip download pip setuptools wheel --dest "$wheelhouse"

# 4. Offline installer + tarball
cat > "$out/install_offline.sh" <<'EOF'
#!/usr/bin/env bash
# Run on the AIR-GAPPED machine. Installs trace32-mcp with zero network.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
python -m pip install --no-index --find-links "$here/wheelhouse" trace32-mcp
echo
echo "Installed. Register the MCP server with:"
echo "  claude mcp add trace32 -s user -- trace32-mcp"
echo "Verify the search path with:  trace32-mcp-selftest --dump-after 0"
EOF
chmod +x "$out/install_offline.sh"

tar -czf "$repo/trace32-mcp-airgap-bundle.tar.gz" -C "$out" .
echo
echo "[bundle] DONE -> $repo/trace32-mcp-airgap-bundle.tar.gz"
echo "[bundle] Copy it to the air-gapped machine, extract, and run install_offline.sh"
