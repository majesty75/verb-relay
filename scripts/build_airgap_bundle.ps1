# Build a self-contained offline bundle for an air-gapped Windows machine.
#
# DEFAULT (recommended): download the published, prebuilt wheel (ONNX model +
# manuals DB already baked in) and all its RUNTIME deps into a wheelhouse. This
# needs NO torch and NO clone — just pip download. Run it on an internet box
# with the SAME OS/arch/Python version as the air-gapped target.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_airgap_bundle.ps1
#
# OPTIONAL: -FromSource builds the wheel from this checkout instead, which
# exports the ONNX model and therefore DOES download torch (one-time, build
# machine only — torch never enters the bundle).
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_airgap_bundle.ps1 -FromSource
#
param(
    [switch]$FromSource,
    [string]$WheelUrl = "https://github.com/majesty75/verb-relay/releases/download/v0.2.0/trace32_mcp-0.2.0-py3-none-any.whl"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$out = Join-Path $repo "airgap_bundle"
$wheelhouse = Join-Path $out "wheelhouse"
Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

if ($FromSource) {
    # Build from this checkout (exports the model -> needs torch on THIS box).
    if (-not (Test-Path "src\trace32_mcp\model\model.onnx")) {
        Write-Host "[bundle] exporting ONNX model (one-time, needs internet + torch)..."
        python -m pip install -e ".[build]"
        python scripts\build_onnx_model.py
    }
    Write-Host "[bundle] building the wheel..."
    python -m pip install -q --upgrade build
    python -m build --wheel --outdir $out
    $wheel = (Get-ChildItem $out -Filter "trace32_mcp-*.whl" | Select-Object -First 1).FullName
    Write-Host "[bundle] downloading runtime deps (no torch)..."
    python -m pip download $wheel --dest $wheelhouse
} else {
    # Torch-free path: pull the published wheel + its runtime deps. No torch.
    Write-Host "[bundle] downloading published wheel + runtime deps (no torch, no clone)..."
    Write-Host "         $WheelUrl"
    python -m pip download $WheelUrl --dest $wheelhouse
}
# pip itself so the offline box can install even without it
python -m pip download pip setuptools wheel --dest $wheelhouse

@'
# Run on the AIR-GAPPED machine. Installs trace32-mcp with zero network.
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
python -m pip install --no-index --find-links "$here\wheelhouse" trace32-mcp
Write-Host ""
Write-Host "Installed (torch-free). Register the MCP server with:"
Write-Host '  claude mcp add trace32 -s user -- trace32-mcp'
Write-Host "Verify:  trace32-mcp-selftest --dump-after 0"
'@ | Set-Content (Join-Path $out "install_offline.ps1")

$zip = Join-Path $repo "trace32-mcp-airgap-bundle.zip"
Remove-Item -Force $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "$out\*" -DestinationPath $zip
Write-Host ""
Write-Host "[bundle] DONE -> $zip"
Write-Host "[bundle] Copy to the air-gapped machine, unzip, run install_offline.ps1"
