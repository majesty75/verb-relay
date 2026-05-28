"""ONNX embedding backend — no torch, no sentence-transformers.

Runtime-only dependency footprint is `onnxruntime` + `tokenizers` + `numpy`,
which makes it trivially air-gappable (onnxruntime CPU is a ~10 MB wheel vs.
torch's hundreds of MB of platform/CUDA-specific binaries).

The model (an ONNX export of BAAI/bge-base-en-v1.5) and its `tokenizer.json`
are vendored under `trace32_mcp/model/` and shipped inside the wheel, so there
is ZERO runtime download. The fp32 export reproduces the sentence-transformers
embeddings exactly (cosine 1.0), so retrieval against the bundled DB is
unchanged.

BGE pooling is CLS (take the first token of last_hidden_state) followed by L2
normalisation; queries get an instruction prefix, passages do not.
"""

from __future__ import annotations

import json
import os

# Disable Hugging Face tokenizer parallelism to prevent subprocess deadlocks
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from pathlib import Path
from typing import Sequence

import numpy as np


def model_dir() -> Path | None:
    """Locate the vendored ONNX model directory.

    Order: T32_MANUALS_ONNX_DIR env override, then the in-package
    `trace32_mcp/model/` (works for both wheel installs and source checkouts).
    Returns None if no `model.onnx` is present.
    """
    override = os.environ.get("T32_MANUALS_ONNX_DIR")
    if override:
        p = Path(override).expanduser()
        return p if (p / "model.onnx").exists() else None
    here = Path(__file__).resolve()
    cand = here.parent.parent / "model"  # trace32_mcp/model
    return cand if (cand / "model.onnx").exists() else None


def onnx_model_available() -> bool:
    return model_dir() is not None


# Default mirrors meta.json; only used if meta.json is missing.
_DEFAULT_QUERY_PREFIX = "Represent this query for retrieving relevant TRACE32 documentation: "


class OnnxEmbedder:
    """Drop-in replacement for the sentence-transformers Embedder.

    Exposes the same surface used by search.py: `.dim`, `.encode_query(text)`
    and `.encode_passages(texts)`.
    """

    def __init__(self, mdir: Path | None = None, batch_size: int = 32,
                 num_threads: int | None = 1) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        d = mdir or model_dir()
        if d is None:
            raise FileNotFoundError(
                "no vendored ONNX model found (trace32_mcp/model/model.onnx). "
                "Build one with scripts/build_onnx_model.py or set "
                "T32_MANUALS_ONNX_DIR."
            )
        self.dir = d
        meta = {}
        meta_path = d / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
        self.query_prefix: str = meta.get("query_prefix", _DEFAULT_QUERY_PREFIX)
        self.max_seq_length: int = int(meta.get("max_seq_length", 512))
        self._input_names: list[str] = meta.get(
            "onnx_inputs", ["input_ids", "attention_mask", "token_type_ids"]
        )
        self._output_name: str = meta.get("onnx_output", "last_hidden_state")
        self.batch_size = batch_size
        self.device = "cpu"

        self._tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self._tok.enable_truncation(max_length=self.max_seq_length)

        so = ort.SessionOptions()
        so.log_severity_level = 3  # Error/Fatal only
        if num_threads:
            so.intra_op_num_threads = int(num_threads)
            so.inter_op_num_threads = int(num_threads)
        self._sess = ort.InferenceSession(
            str(d / "model.onnx"), sess_options=so,
            providers=["CPUExecutionProvider"],
        )
        self.dim = int(meta.get("dim", 0)) or self._probe_dim()

    # -- internal ---------------------------------------------------------
    def _feeds(self, batch_ids: list[list[int]], batch_mask: list[list[int]]) -> dict:
        ids = np.asarray(batch_ids, dtype=np.int64)
        mask = np.asarray(batch_mask, dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        # Only pass inputs the model actually declares.
        return {k: v for k, v in feeds.items() if k in self._input_names}

    def _run_cls_normalised(self, batch_ids, batch_mask) -> np.ndarray:
        out = self._sess.run([self._output_name], self._feeds(batch_ids, batch_mask))[0]
        cls = out[:, 0]  # BGE = CLS pooling
        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (cls / norms).astype(np.float32)

    def _probe_dim(self) -> int:
        enc = self._tok.encode("probe")
        v = self._run_cls_normalised([enc.ids], [enc.attention_mask])
        return int(v.shape[1])

    # -- public (mirrors Embedder) ---------------------------------------
    def encode_query(self, text: str) -> np.ndarray:
        enc = self._tok.encode(self.query_prefix + text)
        return self._run_cls_normalised([enc.ids], [enc.attention_mask])[0]

    def encode_passages(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        out: list[np.ndarray] = []
        for i in range(0, len(texts), self.batch_size):
            chunk = texts[i:i + self.batch_size]
            encs = self._tok.encode_batch(chunk)
            maxlen = max(len(e.ids) for e in encs)
            ids, mask = [], []
            for e in encs:
                pad = maxlen - len(e.ids)
                ids.append(list(e.ids) + [0] * pad)
                mask.append(list(e.attention_mask) + [0] * pad)
            out.append(self._run_cls_normalised(ids, mask))
        return np.vstack(out).astype(np.float32)
