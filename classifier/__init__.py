"""FleeceClassifier — async ONNX inference for Synchilla/Snap-T detection."""
import asyncio
import io
import logging
import os
import time
from pathlib import Path

import httpx
import numpy as np

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/classifier/cache")
CACHE_TTL = 86400  # 24 hours


class FleeceClassifier:
    def __init__(self, model_path: str):
        self.ready = False
        self._session = None
        self._load(model_path)

    def _load(self, model_path: str):
        if not os.path.exists(model_path):
            logger.warning(f"[classifier] Model not found at {model_path} — running keyword-only mode")
            return
        try:
            import onnxruntime as ort
            self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            self.ready = True
            logger.info(f"[classifier] Loaded model from {model_path}")
        except Exception as e:
            logger.error(f"[classifier] Failed to load model: {e}")

    async def classify(self, image_url: str, threshold: float = 0.35) -> dict:
        """Classify listing image. Returns {want: bool, confidence: float}."""
        if not self.ready:
            return {"want": True, "confidence": 0.0}
        if not image_url:
            return {"want": True, "confidence": 0.0}
        try:
            arr = await self._fetch_and_preprocess(image_url)
            loop = asyncio.get_event_loop()
            prob = await loop.run_in_executor(None, self._infer, arr)
            return {"want": prob >= threshold, "confidence": round(prob, 4)}
        except Exception as e:
            logger.warning(f"[classifier] classify error for {image_url}: {e}")
            return {"want": True, "confidence": 0.0}

    def _infer(self, arr: np.ndarray) -> float:
        """Synchronous ONNX inference. Returns want probability."""
        logits = self._session.run(["logits"], {"image": arr})[0]
        exp = np.exp(logits - logits.max())
        probs = exp / exp.sum()
        return float(probs[0][1])  # index 1 = WANT class

    async def _fetch_and_preprocess(self, image_url: str) -> np.ndarray:
        """Download image (with cache) and return normalised NCHW float32 array."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_key = _url_to_cache_key(image_url)
        cache_path = CACHE_DIR / cache_key

        if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < CACHE_TTL:
            img_bytes = cache_path.read_bytes()
        else:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                img_bytes = resp.content
            cache_path.write_bytes(img_bytes)

        return _preprocess(img_bytes)


def _url_to_cache_key(url: str) -> str:
    import hashlib
    return hashlib.md5(url.encode()).hexdigest() + ".npy"


def _preprocess(img_bytes: bytes) -> np.ndarray:
    """Resize to 224x224, normalise with ImageNet stats, return NCHW float32."""
    from PIL import Image
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((224, 224))
    arr = np.array(img, dtype=np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406])
    std = np.array([0.229, 0.224, 0.225])
    arr = (arr - mean) / std
    return arr.transpose(2, 0, 1)[np.newaxis].astype(np.float32)
