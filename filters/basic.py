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


def apply_basic_filters(listings: list[ListingModel], criteria: dict) -> list[ListingModel]:
    filtered = []

    min_price = criteria.get("min_price")
    max_price = criteria.get("max_price")
    district = criteria.get("district")
    min_rooms = criteria.get("min_rooms")
    is_furnished = criteria.get("is_furnished")
    seller_type = criteria.get("seller_type")

    for listing in listings:
        if min_price and listing.price < min_price:
            continue

        if max_price and listing.price > max_price:
            continue

        if district and listing.district:
            allowed_districts = [_normalize_text(d.strip()) for d in district.split(",")]
            listing_district = _normalize_text(listing.district)
            if not any(d and d in listing_district for d in allowed_districts):
                continue

        if min_rooms and listing.room_count:
            try:
                rooms = int(listing.room_count.split("+")[0])
                if rooms < min_rooms:
                    continue
            except ValueError:
                pass

        listing_text = _listing_text(listing)

        if is_furnished is not None:
            listing_furnished = listing.is_furnished
            if listing_furnished is None:
                listing_furnished = _detect_furnished(listing_text)
            if listing_furnished != is_furnished:
                continue

        if seller_type:
            expected_seller = _normalize_text(str(seller_type))
            listing_seller = listing.seller_type or _detect_seller_type(listing_text)
            listing_seller = _normalize_text(str(listing_seller or ""))

            if expected_seller == "sahibinden" and listing_seller != "sahibinden":
                continue
            if expected_seller == "emlak" and listing_seller != "emlak":
                continue

        filtered.append(listing)

    return filtered
