# Offline / air-gapped install

The trace32 MCP runs **fully offline with no torch and no network**. The
embedding model (an ONNX export of BAAI/bge-base-en-v1.5) and the manuals
vector DB are baked into the wheel; embeddings run on `onnxruntime`. So an
air-gapped machine never reaches GitHub, the Hugging Face Hub, or PyPI at
runtime — and the search results are identical to the torch build (the fp32
ONNX export reproduces sentence-transformers embeddings exactly, cosine 1.0).

There are two machines involved:

* **Build machine** — has internet, *same OS/arch/Python version* as the target.
* **Air-gapped machine** — no network at all (can't even reach GitHub).

## 1. On the build machine (one time)

Clone the repo, then build the bundle. The script exports the ONNX model (needs
`torch` once, via the `[build]` extra), builds the self-contained wheel, and
downloads every runtime dependency as a wheel into a `wheelhouse/`.

**Windows (PowerShell):**
```powershell
git clone https://github.com/majesty75/verb-relay.git
cd verb-relay
powershell -ExecutionPolicy Bypass -File scripts\build_airgap_bundle.ps1
# -> trace32-mcp-airgap-bundle.zip
```

**Linux / macOS:**
```bash
git clone https://github.com/majesty75/verb-relay.git
cd verb-relay
bash scripts/build_airgap_bundle.sh
# -> trace32-mcp-airgap-bundle.tar.gz
```

The bundle contains:
```
trace32_mcp-0.1.0-py3-none-any.whl   # model + manuals DB baked in (~350 MB)
wheelhouse/                          # onnxruntime, tokenizers, mcp, pydantic, ... as wheels
install_offline.ps1 / install_offline.sh
```

> The target machine must have a Python interpreter already (3.10+), same major
> version/arch as the build machine, since native wheels (onnxruntime,
> sqlite-vec, lauterbach-trace32-rcl) are platform-specific. If the air-gapped
> Python differs, build the bundle on a machine that matches it.

## 2. Transfer

Copy the single `.zip` / `.tar.gz` to the air-gapped machine (USB, etc.).

## 3. On the air-gapped machine

Extract and run the offline installer — it installs from local wheels only,
with `--no-index` (no network):

**Windows:**
```powershell
Expand-Archive trace32-mcp-airgap-bundle.zip -DestinationPath trace32-mcp-airgap
cd trace32-mcp-airgap
powershell -ExecutionPolicy Bypass -File install_offline.ps1
```

**Linux / macOS:**
```bash
mkdir trace32-mcp-airgap && tar -xzf trace32-mcp-airgap-bundle.tar.gz -C trace32-mcp-airgap
cd trace32-mcp-airgap
bash install_offline.sh
```

Under the hood that's just:
```
pip install --no-index --find-links wheelhouse trace32-mcp
```

## 4. Verify + register

```
trace32-mcp-selftest --dump-after 0      # should print "search path is healthy"
claude mcp add trace32 -s user -- trace32-mcp
```
No `HF_HUB_OFFLINE` or model prefetch is needed — there's nothing to download.

## Notes

* **Manual wheel install** (if you don't want the script): on the build machine
  run `python scripts/build_onnx_model.py` then `python -m build --wheel`, and
  `pip download <wheel> -d wheelhouse`. Ship `dist/*.whl` + `wheelhouse/`.
* **Smaller bundle**: `python scripts/build_onnx_model.py --quant int8` makes the
  model ~110 MB instead of ~437 MB, but slightly shifts retrieval — only do this
  if you also re-embed the DB at int8. Default fp32 is recommended.
* **Forcing a backend**: `T32_MANUALS_BACKEND=onnx` (default when the model is
  present) or `=torch` to use the sentence-transformers fallback.
* **TRACE32 itself** is separate — the MCP still needs a real TRACE32/PowerView
  install on the machine to drive hardware/sim; only the manuals search is
  bundled here.
