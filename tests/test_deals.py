from __future__ import annotations

from nikon_value.deals import DEAL_MAX_PER_PRODUCT, extract_deal_listings
from nikon_value.sitegen.components import DEAL_RADAR_MAX_ITEMS, build_deal_radar
from nikon_value.sitegen.data import merge_catalog_with_config


def _item(price, *, url="https://www.ebay.com/itm/1", title="Nikon Z9 body", shipping=None, image=None):
    item = {
        "title": title,
        "price": {"value": str(price), "currency": "USD"},
        "itemWebUrl": url,
    }
    if shipping is not None:
        item["shippingOptions"] = [{"shippingCost": {"value": str(shipping)}}]
    if image is not None:
        item["thumbnailImages"] = [{"imageUrl": image}]
    return item


def test_extract_deal_listings_requires_valid_median():
    items = [_item(50)]
    assert extract_deal_listings(items, None) == []
    assert extract_deal_listings(items, 0) == []


def test_extract_deal_listings_keeps_only_40_to_80_percent_window():
    median = 1000.0
    items = [
        _item(850),   # 85% — 딜 아님
        _item(800),   # 정확히 80% — 포함 (경계 inclusive)
        _item(500),   # 50% — 포함
        _item(400),   # 정확히 40% — 포함 (경계 inclusive)
        _item(350),   # 35% — 오탐 가능성으로 제외
    ]
    deals = extract_deal_listings(items, median)
    assert [d["price"] for d in deals] == [400.0, 500.0, 800.0][:DEAL_MAX_PER_PRODUCT]


def test_extract_deal_listings_sorted_by_price_and_capped():
    median = 1000.0
    items = [_item(p) for p in (790, 450, 600, 700, 500)]
    deals = extract_deal_listings(items, median)
    assert len(deals) == DEAL_MAX_PER_PRODUCT
    assert [d["price"] for d in deals] == [450.0, 500.0, 600.0]


def test_extract_deal_listings_includes_shipping_and_metadata():
    deals = extract_deal_listings(
        [_item(700, shipping=50, image="https://i.ebayimg.com/x/s-l225.jpg")],
        1000.0,
    )
    assert len(deals) == 1
    deal = deals[0]
    assert deal["price"] == 750.0
    assert deal["discount_pct"] == 25.0
    assert deal["image"] == "https://i.ebayimg.com/x/s-l225.jpg"
    assert deal["url"] == "https://www.ebay.com/itm/1"
    assert deal["title"] == "Nikon Z9 body"


def test_extract_deal_listings_skips_unusable_items():
    items = [
        {"title": "no price", "itemWebUrl": "https://www.ebay.com/itm/2"},
        _item(500, url=""),
    ]
    assert extract_deal_listings(items, 1000.0) == []


def _catalog_with_deals(deal_lists):
    products = []
    for i, deals in enumerate(deal_lists):
        products.append({
            "id": f"prod-{i}",
            "name_ko": f"제품 {i}",
            "name_en": f"Product {i}",
            "median": 1000.0,
            "deals": deals,
        })
    return {"categories": [{"id": "cat", "name_ko": "분류", "name_en": "Cat", "products": products}]}


def _deal(discount_pct, *, url="https://www.ebay.com/itm/9", title="listing"):
    return {
        "title": title,
        "price": 1000.0 * (1 - discount_pct / 100),
        "discount_pct": discount_pct,
        "image": "",
        "url": url,
    }


def test_build_deal_radar_empty_when_no_deals():
    assert build_deal_radar(_catalog_with_deals([[], []])) == ""


def test_build_deal_radar_sorts_by_discount_and_caps():
    deal_lists = [[_deal(20 + i)] for i in range(DEAL_RADAR_MAX_ITEMS + 3)]
    html = build_deal_radar(_catalog_with_deals(deal_lists))
    assert html.count("deal-card ") == 0  # 클래스명 부분 일치 방지용 sanity
    assert html.count('class="deal-card"') == DEAL_RADAR_MAX_ITEMS
    # 가장 큰 할인율이 먼저 나온다
    first_badge = html.index("-34%")
    last_badge = html.index("-23%")
    assert first_badge < last_badge
    assert "-22%" not in html  # cap 밖 (할인율 하위 3개 탈락)


def test_build_deal_radar_escapes_and_skips_missing_url():
    catalog = _catalog_with_deals([
        [_deal(25, title='<script>alert("x")</script>')],
        [_deal(30, url="")],
    ])
    html = build_deal_radar(catalog)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert html.count('class="deal-card"') == 1


def test_merge_catalog_preserves_deals():
    config = {
        "categories": [{
            "id": "cat", "name_ko": "분류", "name_en": "Cat",
            "products": [{"id": "prod-0", "name_ko": "제품", "name_en": "Product"}],
        }],
    }
    live = {
        "updated": "2026-06-10",
        "categories": [{
            "id": "cat",
            "products": [{"id": "prod-0", "median": 1000.0, "deals": [_deal(25)]}],
        }],
    }
    merged = merge_catalog_with_config(live, config)
    product = merged["categories"][0]["products"][0]
    assert product["deals"] == [_deal(25)]

    merged_empty = merge_catalog_with_config({"categories": []}, config)
    assert merged_empty["categories"][0]["products"][0]["deals"] == []


def test_deal_prefers_affiliate_url_when_present():
    """EPN 헤더가 설정된 경우 커미션이 잡히는 itemAffiliateWebUrl을 써야 한다."""
    affiliate_url = "https://www.ebay.com/itm/111?mkcid=1&campid=5338888888"
    item = _item(60, url="https://www.ebay.com/itm/111")
    item["itemAffiliateWebUrl"] = affiliate_url

    deals = extract_deal_listings([item], 100.0)

    assert deals[0]["url"] == affiliate_url


def test_deal_falls_back_to_plain_url_without_affiliate_field():
    """EPN 미설정 시에는 itemAffiliateWebUrl이 없으므로 기존 동작을 유지한다."""
    deals = extract_deal_listings([_item(60, url="https://www.ebay.com/itm/222")], 100.0)

    assert deals[0]["url"] == "https://www.ebay.com/itm/222"
