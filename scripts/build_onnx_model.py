#!/usr/bin/env python
"""Export the embedding model to ONNX and vendor it into the package.

Run this ONCE on an internet-connected machine (it needs torch +
sentence-transformers + onnx, i.e. `pip install -e .[build]`). It writes:

    src/trace32_mcp/model/model.onnx        (the encoder)
    src/trace32_mcp/model/tokenizer.json    (the tokenizer)
    src/trace32_mcp/model/meta.json         (prefix, pooling, dim, ...)

After this, building the wheel bundles the model and the runtime needs neither
torch nor any network. The fp32 export reproduces sentence-transformers
embeddings exactly (cosine 1.0); --quant int8 is ~4x smaller (~110 MB) with a
tiny retrieval shift, only recommended if you also re-embed the DB at int8.

    python scripts/build_onnx_model.py                # fp32 (default)
    python scripts/build_onnx_model.py --quant int8   # smaller, lossy
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

QUERY_PREFIX = "Represent this query for retrieving relevant TRACE32 documentation: "


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="BAAI/bge-base-en-v1.5")
    ap.add_argument("--quant", choices=["fp32", "int8"], default="fp32")
    ap.add_argument("--out", default=None,
                    help="Output dir (default: src/trace32_mcp/model next to this repo).")
    ap.add_argument("--max-seq-length", type=int, default=512)
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[1]
    out = Path(args.out).expanduser() if args.out else repo / "src" / "trace32_mcp" / "model"
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "_export_tmp"
    tmp.mkdir(exist_ok=True)

    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    print(f"[build] loading {args.model} ...")
    st = SentenceTransformer(args.model, device="cpu")
    tok = st.tokenizer
    hf = st[0].auto_model
    hf.eval()
    pool_cfg = st[1].get_config_dict()
    assert pool_cfg.get("pooling_mode") == "cls", \
        f"expected CLS pooling, got {pool_cfg.get('pooling_mode')!r} — update onnx_embed pooling"

    # --- export to ONNX via the dynamo exporter ------------------------
    sample = ["export probe sentence one", "second probe"]
    enc = tok(sample, padding=True, truncation=True, max_length=args.max_seq_length,
              return_tensors="pt")

    class Wrapper(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask, token_type_ids):
            return self.m(input_ids=input_ids, attention_mask=attention_mask,
                          token_type_ids=token_type_ids).last_hidden_state

    w = Wrapper(hf).eval()
    inp = (enc["input_ids"], enc["attention_mask"],
           enc.get("token_type_ids", torch.zeros_like(enc["input_ids"])))
    dim0, dim1 = torch.export.Dim("batch"), torch.export.Dim("seq")
    dyn = {"input_ids": {0: dim0, 1: dim1},
           "attention_mask": {0: dim0, 1: dim1},
           "token_type_ids": {0: dim0, 1: dim1}}
    fp32_path = tmp / "model.onnx"
    print("[build] exporting ONNX (dynamo) ...")
    prog = torch.onnx.export(
        w, inp, dynamo=True,
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["last_hidden_state"], dynamic_shapes=dyn,
    )
    prog.optimize()
    prog.save(str(fp32_path))
    print(f"[build] fp32 ONNX: {fp32_path.stat().st_size/1e6:.0f} MB")

    final = out / "model.onnx"
    if args.quant == "int8":
        from onnxruntime.quantization import QuantType, quantize_dynamic
        print("[build] quantizing to int8 ...")
        quantize_dynamic(str(fp32_path), str(final), weight_type=QuantType.QInt8)
    else:
        shutil.move(str(fp32_path), str(final))
    print(f"[build] wrote {final} ({final.stat().st_size/1e6:.0f} MB)")

    # --- tokenizer + meta ----------------------------------------------
    tok.save_pretrained(str(tmp / "tok"))
    tj = next((tmp / "tok").glob("tokenizer.json"), None)
    if tj is None:
        raise SystemExit("tokenizer.json not produced by save_pretrained")
    shutil.copy(tj, out / "tokenizer.json")

    dim = int(st.get_sentence_embedding_dimension())
    meta = {
        "model_name": args.model, "format": "onnx", "quantization": args.quant,
        "dim": dim, "pooling": "cls", "normalize": True,
        "max_seq_length": args.max_seq_length, "query_prefix": QUERY_PREFIX,
        "onnx_inputs": ["input_ids", "attention_mask", "token_type_ids"],
        "onnx_output": "last_hidden_state",
        "note": "Built by scripts/build_onnx_model.py.",
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))

    # --- self-check: cosine vs sentence-transformers -------------------
    import onnxruntime as ort
    sess = ort.InferenceSession(str(final), providers=["CPUExecutionProvider"])
    texts = [QUERY_PREFIX + "hardware breakpoint cortex-m7", "ETM instruction trace"]
    e = tok(texts, padding=True, truncation=True, max_length=args.max_seq_length, return_tensors="np")
    feeds = {"input_ids": e["input_ids"].astype(np.int64),
             "attention_mask": e["attention_mask"].astype(np.int64),
             "token_type_ids": e.get("token_type_ids", np.zeros_like(e["input_ids"])).astype(np.int64)}
    cls = sess.run(["last_hidden_state"], feeds)[0][:, 0]
    onnx_vec = cls / np.linalg.norm(cls, axis=1, keepdims=True)
    ref = st.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    cos = (ref * onnx_vec).sum(1)
    print(f"[build] cosine vs sentence-transformers: {np.round(cos, 5)} "
          f"({'exact' if cos.min() > 0.999 else 'approx (int8)'})")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"[build] done. Vendored model in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
