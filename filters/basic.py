import re
import unicodedata

from scraper.normalizer import ListingModel

_TR_TRANSLATION = str.maketrans({
    "\u00c7": "c", "\u00e7": "c",
    "\u011e": "g", "\u011f": "g",
    "\u0130": "i", "\u0131": "i",
    "\u00d6": "o", "\u00f6": "o",
    "\u015e": "s", "\u015f": "s",
    "\u00dc": "u", "\u00fc": "u",
})


def _normalize_text(value: str) -> str:
    value = value or ""
    try:
        repaired = value.encode("latin1").decode("utf-8")
        if repaired != value:
            value = f"{value} {repaired}"
    except UnicodeError:
        pass

    value = value.translate(_TR_TRANSLATION)
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    return " ".join(value.lower().split())


def _listing_text(listing: ListingModel) -> str:
    return " ".join(
        value or ""
        for value in (
            listing.title,
            listing.district,
            listing.room_count,
            listing.building_age,
            listing.description,
            listing.url,
        )
    )


def _detect_furnished(text: str):
    normalized = _normalize_text(text)

    if any(token in normalized for token in ("esyali degil", "esyasiz", "esya yok", "mobilyasiz")):
        return False
    if any(token in normalized for token in ("esyali", "mobilyali", "full esya", "esya dahil")):
        return True
    return None


def _detect_seller_type(text: str):
    normalized = _normalize_text(text)

    if "sahibinden" in normalized or "ev sahibinden" in normalized:
        return "sahibinden"

    agency_tokens = (
        "emlakci",
        "emlak ofisi",
        "emlak firmasi",
        "gayrimenkul",
        "danisman",
        "danismani",
        "ofis",
        "acente",
        "remax",
        "coldwell",
        "kw ",
        "realty",
        "broker",
        "portfoy",
    )
    if any(token in normalized for token in agency_tokens):
        return "emlak"

    if re.search(r"\b[a-z0-9]+ emlak\b", normalized):
        return "emlak"

    return None


def _parse_room_number(value: str):
    match = re.search(r"(\d+(?:[,.]\d+)?)\s*\+\s*\d+", value or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _parse_building_age(value: str):
    normalized = _normalize_text(value or "")
    if not normalized:
        return None
    if any(token in normalized for token in ("sifir", "yeni")) or re.fullmatch(r"0(?:\s*-\s*5)?", normalized):
        return 0
    match = re.search(r"\d+", normalized)
    return int(match.group(0)) if match else None


def apply_basic_filters(listings: list[ListingModel], criteria: dict) -> list[ListingModel]:
    filtered = []

    min_price = criteria.get("min_price")
    max_price = criteria.get("max_price")
    district = criteria.get("district")
    min_rooms = criteria.get("min_rooms")
    max_rooms = criteria.get("max_rooms")
    max_building_age = criteria.get("max_building_age")
    is_furnished = criteria.get("is_furnished")
    seller_type = criteria.get("seller_type")

    for listing in listings:
        if (min_price or max_price) and not listing.price:
            continue

        if min_price and listing.price < min_price:
            continue

        if max_price and listing.price > max_price:
            continue

        if district:
            if not listing.district:
                continue
            allowed_districts = [_normalize_text(d.strip()) for d in district.split(",")]
            listing_district = _normalize_text(listing.district)
            if not any(d and d in listing_district for d in allowed_districts):
                continue

        if min_rooms or max_rooms:
            rooms = _parse_room_number(listing.room_count or "")
            if rooms is None:
                continue
            if min_rooms and rooms < float(min_rooms):
                continue
            if max_rooms and rooms > float(max_rooms):
                continue

        if max_building_age is not None:
            building_age = _parse_building_age(listing.building_age or "")
            if building_age is None or building_age > int(max_building_age):
                continue

        if is_furnished is not None:
            if listing.is_furnished != is_furnished:
                continue

        if seller_type:
            expected_seller = _normalize_text(str(seller_type))
            listing_seller = _normalize_text(str(listing.seller_type or ""))

            if expected_seller == "sahibinden" and listing_seller != "sahibinden":
                continue
            if expected_seller == "emlak" and listing_seller != "emlak":
                continue

        filtered.append(listing)

    return filtered
