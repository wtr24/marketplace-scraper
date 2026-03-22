# tests/test_classifier.py
import numpy as np
from unittest.mock import MagicMock
from classifier import FleeceClassifier


def test_classifier_ready_false_when_no_model():
    clf = FleeceClassifier("nonexistent_model.onnx")
    assert clf.ready is False


def test_classifier_classify_returns_want_true_when_not_ready():
    clf = FleeceClassifier("nonexistent_model.onnx")
    import asyncio
    result = asyncio.run(clf.classify("http://example.com/img.jpg"))
    assert result["want"] is True
    assert "confidence" in result


def test_classifier_infer_returns_probability():
    clf = FleeceClassifier("nonexistent_model.onnx")
    clf.ready = True
    mock_session = MagicMock()
    mock_session.run.return_value = [np.array([[1.0, 3.0]])]  # logits: dont_want=1, want=3
    clf._session = mock_session
    prob = clf._infer(np.zeros((1, 3, 224, 224), dtype=np.float32))
    assert 0.5 < prob < 1.0  # want logit higher → prob > 0.5


def test_filter_by_classifier_rejects_unwanted():
    """_filter_by_classifier should remove listings where classifier returns want=False."""
    import asyncio
    from unittest.mock import AsyncMock, patch, MagicMock

    mock_clf = MagicMock()
    mock_clf.ready = True
    mock_clf.classify = AsyncMock(return_value={"want": False, "confidence": 0.9})

    listings = [{"id": 1, "title": "Patagonia Synchilla fleece", "image_url": "http://x.com/img.jpg",
                 "site": "vinted", "price": 30}]

    with patch("scheduler.classifier", mock_clf):
        from scheduler import _filter_by_classifier
        filtered = asyncio.run(_filter_by_classifier(listings))
        assert filtered == []
