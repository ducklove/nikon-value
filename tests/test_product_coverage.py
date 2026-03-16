from scripts.build_static_site import load_catalog_config, should_show_home_catalog_product


EXPECTED_F_MOUNT_DSLR_IDS = {
    "nikon-e2-e2s",
    "nikon-e2n-e2ns",
    "nikon-e3-e3s",
    "nikon-d1",
    "nikon-d1h",
    "nikon-d1x",
    "nikon-d100",
    "nikon-d2h",
    "nikon-d2hs",
    "nikon-d2x",
    "nikon-d2xs",
    "nikon-d3",
    "nikon-d3s",
    "nikon-d3x",
    "nikon-d4",
    "nikon-d4s",
    "nikon-d5",
    "nikon-d6",
    "nikon-d40",
    "nikon-d40x",
    "nikon-d50",
    "nikon-d60",
    "nikon-d70",
    "nikon-d70s",
    "nikon-d80",
    "nikon-d90",
    "nikon-d200",
    "nikon-d300",
    "nikon-d300s",
    "nikon-d500",
    "nikon-d600",
    "nikon-d610",
    "nikon-d700",
    "nikon-d7000",
    "nikon-d7100",
    "nikon-d7200",
    "nikon-d750",
    "nikon-d7500",
    "nikon-d780",
    "nikon-d800",
    "nikon-d800e",
    "nikon-d810",
    "nikon-d810a",
    "nikon-d850",
    "nikon-d3000",
    "nikon-d3100",
    "nikon-d3200",
    "nikon-d3300",
    "nikon-d3400",
    "nikon-d3500",
    "nikon-d5000",
    "nikon-d5100",
    "nikon-d5200",
    "nikon-d5300",
    "nikon-d5500",
    "nikon-d5600",
    "nikon-df",
}


def test_f_mount_dslr_catalog_is_complete_and_unique():
    config = load_catalog_config()
    dslr_category = next(category for category in config["categories"] if category["id"] == "f-mount-dslr")
    product_ids = [product["id"] for product in dslr_category["products"]]

    assert len(product_ids) == len(set(product_ids))

    missing = EXPECTED_F_MOUNT_DSLR_IDS - set(product_ids)
    assert not missing, f"Missing Nikon DSLR bodies: {sorted(missing)}"


def test_home_catalog_still_shows_dslrs_without_listing_data():
    assert should_show_home_catalog_product("f-mount-dslr", {"count": 0})
    assert not should_show_home_catalog_product("classic-lenses", {"count": 0})
    assert should_show_home_catalog_product("classic-lenses", {"count": 1})
