from __future__ import annotations

from typing import Sequence

import numpy as np

from .config import resolve_device


class Embedder:
    """Wraps sentence-transformers BGE-base for retrieval embeddings.

    BGE models recommend an instruction prefix for queries but not for passages,
    which is what we follow here.
    """

    QUERY_PREFIX = "Represent this query for retrieving relevant TRACE32 documentation: "

    def __init__(self, model_name: str, device: str = "auto", batch_size: int = 32,
                 local_files_only: bool = False) -> None:
        from sentence_transformers import SentenceTransformer

        resolved = resolve_device(device)
        self.device = resolved
        # When the model is already cached we force local_files_only so
        # SentenceTransformer/huggingface_hub never makes a revision-check
        # network call. On a locked-down corporate network that probe can hang
        # for minutes (HF retry/backoff) even though the weights are local —
        # that, not the download, is the usual "search never returns" cause.
        self.model = SentenceTransformer(
            model_name, device=resolved, local_files_only=local_files_only
        )
        self.batch_size = batch_size
        # `get_sentence_embedding_dimension` is deprecated in sentence-transformers
        # 5.x (renamed to `get_embedding_dimension`); use the new name when
        # present so we don't depend on the deprecated alias.
        get_dim = getattr(self.model, "get_embedding_dimension", None) or \
            self.model.get_sentence_embedding_dimension
        self.dim = int(get_dim())

    def encode_passages(self, texts: Sequence[str]) -> np.ndarray:
        return np.asarray(
            self.model.encode(
                list(texts),
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            ),
            dtype=np.float32,
        )

    def encode_query(self, text: str) -> np.ndarray:
        return np.asarray(
            self.model.encode(
                [self.QUERY_PREFIX + text],
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )[0],
            dtype=np.float32,
        )
