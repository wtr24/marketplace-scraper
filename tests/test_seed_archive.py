# tests/test_seed_archive.py
import json
from classifier.seed_archive import extract_images_from_shopify_response

def test_extract_images_from_shopify_response():
    fake_response = {
        "products": [
            {
                "title": "1992 Diamonds Synchilla Snap-T",
                "images": [{"src": "https://cdn.shopify.com/test/image1.jpg"}]
            },
            {
                "title": "No Image Product",
                "images": []
            }
        ]
    }
    results = extract_images_from_shopify_response(fake_response)
    assert len(results) == 1
    assert results[0]["title"] == "1992 Diamonds Synchilla Snap-T"
    assert results[0]["image_url"] == "https://cdn.shopify.com/test/image1.jpg"
    assert results[0]["label"] == "want"
    assert results[0]["source"] == "archive"
