"""Optional local multilingual embeddings (fastembed / ONNX, no API key).

MEMORY_EMBEDDINGS=auto (default: use if model available/downloadable) | on | off
Model: paraphrase-multilingual-MiniLM-L12-v2 (384-d, Arabic+English, ~240MB in data/models).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import List, Optional

import numpy as np

from agent.config_store import DATA_DIR

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIM = 384


class Embedder:
    def __init__(self):
        self.mode = os.getenv("MEMORY_EMBEDDINGS", "auto").strip().lower()
        self._model = None
        self._failed: Optional[str] = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is not None or self._failed or self.mode == "off":
            return
        with self._lock:
            if self._model is not None or self._failed:
                return
            try:
                import warnings
                from fastembed import TextEmbedding

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    self._model = TextEmbedding(MODEL_NAME, cache_dir=str(DATA_DIR / "models"), threads=1)
                logger.info("Embeddings ready: %s", MODEL_NAME)
            except Exception as e:  # missing package, no network for first download, etc.
                self._failed = str(e)[:200]
                logger.warning("Embeddings unavailable (%s) — memory search falls back to FTS only", self._failed)

    def available(self) -> bool:
        self._load()
        return self._model is not None

    def status(self) -> dict:
        return {"mode": self.mode, "model": MODEL_NAME, "dim": DIM, "loaded": self._model is not None, "error": self._failed}

    def warmup_async(self) -> None:
        threading.Thread(target=self._load, daemon=True, name="embed-warmup").start()

    def embed(self, texts: List[str]) -> Optional[np.ndarray]:
        if not texts or not self.available():
            return None
        vecs = np.array(list(self._model.embed([t[:2000] for t in texts])), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


_embedder: Optional[Embedder] = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
