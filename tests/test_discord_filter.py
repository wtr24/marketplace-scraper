"""Tests for the Discord notification filter — matches_keyword()."""
import pytest
from discord_webhook import matches_keyword

def listing(title, size=""):
    return {"title": title, "size": size, "description": ""}

# ── Must-have: patagonia + synchilla/snap-t ───────────────────────────────

def test_passes_synchilla():
    assert matches_keyword(listing("Patagonia Synchilla Snap-T Pullover", "L"))

def test_passes_snap_t():
    assert matches_keyword(listing("Patagonia Snap-T Fleece", "M"))

def test_passes_snap_t_no_hyphen():
    assert matches_keyword(listing("Patagonia Snap T Fleece Pullover", "XL"))

def test_rejects_no_patagonia():
    assert not matches_keyword(listing("Columbia Fleece Synchilla Jacket", "L"))

def test_rejects_non_patagonia_fleece():
    assert not matches_keyword(listing("North Face Fleece Jacket", "M"))

def test_rejects_patagonia_no_synchilla_or_snap():
    assert not matches_keyword(listing("Patagonia Nano Puff Jacket", "L"))

def test_rejects_patagonia_plain_fleece():
    assert not matches_keyword(listing("Patagonia Fleece Jacket", "L"))

# ── Size filtering ────────────────────────────────────────────────────────

def test_rejects_xs_size_field():
    assert not matches_keyword(listing("Patagonia Synchilla Snap-T", "XS"))

def test_rejects_s_size_field():
    assert not matches_keyword(listing("Patagonia Synchilla Snap-T", "S"))

def test_rejects_small_size_field():
    assert not matches_keyword(listing("Patagonia Synchilla Snap-T", "Small"))

def test_passes_l():
    assert matches_keyword(listing("Patagonia Synchilla Snap-T", "L"))

def test_passes_m():
    assert matches_keyword(listing("Patagonia Synchilla Snap-T", "M"))

def test_passes_xl():
    assert matches_keyword(listing("Patagonia Synchilla Snap-T", "XL"))

def test_passes_no_size_field():
    # No size info — pass through (can't know)
    assert matches_keyword(listing("Patagonia Synchilla Snap-T Pullover", ""))

def test_rejects_xs_in_title_no_size_field():
    assert not matches_keyword(listing("Patagonia Synchilla Snap-T XS", ""))

# ── Kids clothing ─────────────────────────────────────────────────────────

def test_rejects_kids():
    assert not matches_keyword(listing("Patagonia Synchilla Snap-T Kids Size 12", ""))

def test_rejects_aged():
    assert not matches_keyword(listing("Patagonia Synchilla Fleece Aged 6-8", ""))

def test_rejects_months():
    assert not matches_keyword(listing("Patagonia Synchilla Baby 6 months", ""))

def test_rejects_boys():
    assert not matches_keyword(listing("Patagonia Synchilla Snap-T Boys", "M"))

def test_rejects_toddler():
    assert not matches_keyword(listing("Patagonia Synchilla Toddler", ""))

def test_rejects_youth():
    assert not matches_keyword(listing("Patagonia Synchilla Snap-T Youth", ""))

# ── Style exclusions ──────────────────────────────────────────────────────

def test_rejects_hooded():
    assert not matches_keyword(listing("Patagonia Synchilla Hooded Fleece", "L"))

def test_rejects_hoodie():
    assert not matches_keyword(listing("Patagonia Snap-T Hoodie Pullover", "M"))

def test_rejects_full_zip():
    assert not matches_keyword(listing("Patagonia Synchilla Full Zip Fleece", "L"))

def test_rejects_full_zip_hyphen():
    assert not matches_keyword(listing("Patagonia Snap-T Full-Zip", "XL"))

def test_rejects_gilet():
    assert not matches_keyword(listing("Patagonia Synchilla Gilet", "L"))

def test_rejects_bodywarmer():
    assert not matches_keyword(listing("Patagonia Synchilla Bodywarmer", "M"))

# ── Premium override (rare/vintage/multicolour bypass style exclusions) ──

def test_vintage_overrides_hooded():
    assert matches_keyword(listing("Vintage Patagonia Synchilla Hooded Fleece", "L"))

def test_rare_overrides_full_zip():
    assert matches_keyword(listing("Rare Patagonia Snap-T Full Zip 1994", "XL"))

def test_multicolour_overrides_gilet():
    assert matches_keyword(listing("Patagonia Synchilla Multicolour Gilet", "L"))

def test_multicolored_passes():
    assert matches_keyword(listing("Patagonia Synchilla Snap-T Multicolored", "M"))

def test_vintage_does_not_override_kids():
    # Vintage is no excuse for kids clothing
    assert not matches_keyword(listing("Vintage Patagonia Synchilla Kids", ""))

def test_vintage_does_not_override_xs():
    assert not matches_keyword(listing("Vintage Patagonia Synchilla Snap-T", "XS"))

def test_vintage_does_not_override_no_patagonia():
    assert not matches_keyword(listing("Vintage Columbia Synchilla Fleece", "L"))
