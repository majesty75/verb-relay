#!/usr/bin/env bash
# Build a self-contained offline bundle for an air-gapped Linux/macOS machine.
#
# DEFAULT (recommended): download the published, prebuilt wheel (ONNX model +
# manuals DB already baked in) and all its RUNTIME deps into a wheelhouse. No
# torch, no clone — just pip download. Run on an internet box with the SAME
# OS/arch/Python version as the air-gapped target.
#
#   bash scripts/build_airgap_bundle.sh
#
# OPTIONAL: --from-source builds the wheel from this checkout instead, which
# exports the ONNX model and therefore DOES download torch (one-time, build
# machine only — torch never enters the bundle).
#
#   bash scripts/build_airgap_bundle.sh --from-source
#
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
out="$repo/airgap_bundle"; wheelhouse="$out/wheelhouse"
rm -rf "$out"; mkdir -p "$wheelhouse"
FROM_SOURCE=0; [ "${1:-}" = "--from-source" ] && FROM_SOURCE=1
WHEEL_URL="${WHEEL_URL:-https://github.com/majesty75/verb-relay/releases/download/v0.2.0/trace32_mcp-0.2.0-py3-none-any.whl}"

if [ "$FROM_SOURCE" = "1" ]; then
  if [ ! -f src/trace32_mcp/model/model.onnx ]; then
    echo "[bundle] exporting ONNX model (one-time, needs internet + torch)..."
    python -m pip install -e ".[build]"
    python scripts/build_onnx_model.py
  fi
  echo "[bundle] building the wheel..."
  python -m pip install -q --upgrade build
  python -m build --wheel --outdir "$out"
  wheel="$(ls "$out"/trace32_mcp-*.whl | head -1)"
  echo "[bundle] downloading runtime deps (no torch)..."
  python -m pip download "$wheel" --dest "$wheelhouse"
else
  echo "[bundle] downloading published wheel + runtime deps (no torch, no clone)..."
  echo "         $WHEEL_URL"
  python -m pip download "$WHEEL_URL" --dest "$wheelhouse"
fi
python -m pip download pip setuptools wheel --dest "$wheelhouse"

cat > "$out/install_offline.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
python -m pip install --no-index --find-links "$here/wheelhouse" trace32-mcp
echo; echo "Installed (torch-free). Register with:"
echo "  claude mcp add trace32 -s user -- trace32-mcp"
echo "Verify:  trace32-mcp-selftest --dump-after 0"
EOF
chmod +x "$out/install_offline.sh"

tar -czf "$repo/trace32-mcp-airgap-bundle.tar.gz" -C "$out" .
echo; echo "[bundle] DONE -> $repo/trace32-mcp-airgap-bundle.tar.gz"
echo "[bundle] Copy to the air-gapped machine, extract, run install_offline.sh"
