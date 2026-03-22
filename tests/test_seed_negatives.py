# tests/test_seed_negatives.py
from classifier.seed_negatives import is_synchilla_title

def test_is_synchilla_title_matches():
    assert is_synchilla_title("Patagonia Synchilla Snap-T Blue") is True
    assert is_synchilla_title("patagonia snap t fleece") is True
    assert is_synchilla_title("Patagonia Fleece Jacket") is True

def test_is_synchilla_title_no_match():
    assert is_synchilla_title("North Face fleece jacket") is False
    assert is_synchilla_title("Adidas tracksuit top") is False
    assert is_synchilla_title("Patagonia Nano Puff") is False
