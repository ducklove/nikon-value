from scripts.fetch_prices import is_obvious_non_match, matches_product_exclude_patterns, normalize_title


def test_matches_product_exclude_patterns_uses_normalized_title_fragments():
    product = {"exclude_title_patterns": ["ai-s", "200mm"]}
    normalized_title = normalize_title("NIKON Ai-S 200mm F3.5 AF ED IF F3AF")

    assert matches_product_exclude_patterns(normalized_title, product)


def test_is_obvious_non_match_excludes_f3af_compatibility_lens_listing():
    product = {
        "id": "nikon-f3af",
        "category_id": "3323",
        "exclude_title_patterns": ["ai-s", "80mm", "200mm"],
    }

    assert is_obvious_non_match("NIKON Ai-S 200mm F3.5 AF ED IF F3AF #C1 #101721", product)


def test_is_obvious_non_match_keeps_actual_f3af_body_listing():
    product = {
        "id": "nikon-f3af",
        "category_id": "3323",
        "exclude_title_patterns": ["ai-s", "80mm", "200mm"],
    }

    assert not is_obvious_non_match("Nikon F3AF body film camera", product)
