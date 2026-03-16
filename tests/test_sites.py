"""Unit tests for site adapter normalisation and URL building."""
import pytest
from sites.vinted import build_url as vinted_url, normalise as vinted_norm, extract_listing_id as vinted_id
from sites.ebay import build_url as ebay_url, normalise as ebay_norm, extract_listing_id as ebay_id
from sites.depop import build_url as depop_url, normalise as depop_norm, extract_listing_id as depop_id


class TestVinted:
    def test_build_url(self):
        url = vinted_url("vintage jacket")
        assert "vinted.co.uk" in url
        assert "vintage+jacket" in url or "vintage%20jacket" in url
        assert "newest_first" in url

    def test_extract_listing_id(self):
        assert vinted_id("https://www.vinted.co.uk/items/1234567890-some-jacket") == "1234567890"

    def test_normalise_basic(self):
        raw = {
            "title": " Levi Jacket ",
            "price": "£24.99",
            "url": "https://www.vinted.co.uk/items/999-levi-jacket",
            "brand": "Levi's",
            "size": "M",
            "condition": "Good",
            "seller": "alice",
            "image_url": "https://images.vinted.net/img/abc.jpg",
        }
        result = vinted_norm(raw)
        assert result["site"] == "vinted"
        assert result["listing_id"] == "999"
        assert result["title"] == "Levi Jacket"
        assert result["price"] == 24.99
        assert result["brand"] == "Levi's"
        assert result["size"] == "M"
        assert result["seller"] == "alice"

    def test_normalise_price_integer(self):
        raw = {"price": 15, "url": "https://www.vinted.co.uk/items/1-title"}
        result = vinted_norm(raw)
        assert result["price"] == 15.0

    def test_normalise_missing_price(self):
        raw = {"url": "https://www.vinted.co.uk/items/2-title"}
        result = vinted_norm(raw)
        assert result["price"] is None


class TestEbay:
    def test_build_url(self):
        url = ebay_url("nike trainers")
        assert "ebay.co.uk" in url
        assert "_sop=10" in url

    def test_extract_listing_id_itm(self):
        assert ebay_id("https://www.ebay.co.uk/itm/item-name/123456789012") == "123456789012"

    def test_extract_listing_id_short(self):
        assert ebay_id("https://www.ebay.co.uk/itm/123456789012") == "123456789012"

    def test_normalise(self):
        raw = {
            "title": "Nike Air Max 90",
            "price": "£89.99",
            "condition": "Brand New",
            "url": "https://www.ebay.co.uk/itm/nike-air-max/987654321098",
            "image_url": "https://i.ebayimg.com/img.jpg",
        }
        result = ebay_norm(raw)
        assert result["site"] == "ebay"
        assert result["listing_id"] == "987654321098"
        assert result["price"] == 89.99
        assert result["condition"] == "Brand New"


class TestDepop:
    def test_build_url(self):
        url = depop_url("y2k top")
        assert "depop.com" in url
        assert "newlyListed" in url

    def test_extract_listing_id_with_numeric(self):
        result = depop_id("https://www.depop.com/products/seller-cool-top-12345678/")
        assert result == "12345678"

    def test_extract_listing_id_slug(self):
        result = depop_id("https://www.depop.com/products/seller-coolshirt/")
        assert result == "seller-coolshirt"

    def test_normalise_api_format(self):
        raw = {
            "id": "abc123",
            "description": "Cool Y2K top",
            "price": {"amount": 1500, "currencyName": "GBP"},
            "seller": {"username": "bob"},
            "preview": {"images": [{"url": "https://img.depop.com/abc.jpg"}]},
            "slug": "bob-cool-y2k-top",
        }
        result = depop_norm(raw)
        assert result["site"] == "depop"
        assert result["listing_id"] == "abc123"
        assert result["price"] == 15.0  # 1500 pence -> £15
        assert result["seller"] == "bob"
        assert result["image_url"] == "https://img.depop.com/abc.jpg"
