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

    def __init__(self, model_name: str, device: str = "auto", batch_size: int = 32) -> None:
        from sentence_transformers import SentenceTransformer

        resolved = resolve_device(device)
        self.device = resolved
        self.model = SentenceTransformer(model_name, device=resolved)
        self.batch_size = batch_size
        self.dim = int(self.model.get_sentence_embedding_dimension())

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
