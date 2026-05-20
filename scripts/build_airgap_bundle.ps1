# Build a self-contained offline bundle for an air-gapped Windows machine.
#
# Run this on an INTERNET-CONNECTED Windows box with the SAME Python
# version/arch as the target air-gapped machine. It produces a single folder
# (and .zip) containing:
#   * the trace32-mcp wheel (with the ONNX model + manuals DB baked in)
#   * a wheelhouse of every runtime dependency as .whl files
#   * an install script for the offline machine
#
# Transfer the .zip to the air-gapped machine and run install_offline.ps1.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_airgap_bundle.ps1
#
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$out = Join-Path $repo "airgap_bundle"
$wheelhouse = Join-Path $out "wheelhouse"
Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

# 1. Make sure the ONNX model is vendored (needs the [build] extra: torch etc.)
if (-not (Test-Path "src\trace32_mcp\model\model.onnx")) {
    Write-Host "[bundle] ONNX model missing — exporting it (one-time, needs internet + torch)..."
    python -m pip install -e ".[build]"
    python scripts\build_onnx_model.py
}

# 2. Build the wheel (model + DB baked in via package-data)
Write-Host "[bundle] building the trace32-mcp wheel..."
python -m pip install -q --upgrade build
python -m build --wheel --outdir $out
$wheel = Get-ChildItem $out -Filter "trace32_mcp-*.whl" | Select-Object -First 1

# 3. Download all RUNTIME deps for this platform into the wheelhouse
Write-Host "[bundle] downloading runtime dependencies into the wheelhouse..."
python -m pip download $wheel.FullName --dest $wheelhouse
# pip itself, so the offline box can install even without it
python -m pip download pip setuptools wheel --dest $wheelhouse

# 4. Write the offline installer + zip everything
@'
# Run on the AIR-GAPPED machine. Installs trace32-mcp with zero network.
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
python -m pip install --no-index --find-links "$here\wheelhouse" trace32-mcp
Write-Host ""
Write-Host "Installed. Register the MCP server with:"
Write-Host '  claude mcp add trace32 -s user -- trace32-mcp'
Write-Host "Verify the search path with:  trace32-mcp-selftest --dump-after 0"
'@ | Set-Content (Join-Path $out "install_offline.ps1")

$zip = Join-Path $repo "trace32-mcp-airgap-bundle.zip"
Remove-Item -Force $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "$out\*" -DestinationPath $zip
Write-Host ""
Write-Host "[bundle] DONE -> $zip"
Write-Host "[bundle] Copy it to the air-gapped machine, unzip, and run install_offline.ps1"
