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


def _evaluate_listing(listing: ListingModel, criteria: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    min_price = criteria.get("min_price")
    max_price = criteria.get("max_price")
    district = criteria.get("district")
    min_rooms = criteria.get("min_rooms")
    max_rooms = criteria.get("max_rooms")
    max_building_age = criteria.get("max_building_age")
    is_furnished = criteria.get("is_furnished")
    seller_type = criteria.get("seller_type")

    if (min_price or max_price) and not listing.price:
        return False, ["fiyat_bilgisi_yok"]

    if min_price and listing.price < min_price:
        return False, [f"fiyat_min_alti({listing.price}<{min_price})"]

    if max_price and listing.price > max_price:
        return False, [f"fiyat_max_ustu({listing.price}>{max_price})"]

    if district:
        if not listing.district:
            return False, ["ilce_bilgisi_yok"]
        allowed_districts = [_normalize_text(d.strip()) for d in district.split(",")]
        listing_district = _normalize_text(listing.district)
        if not any(d and d in listing_district for d in allowed_districts):
            return False, [f"ilce_uyusmuyor({listing.district})"]

    if min_rooms or max_rooms:
        rooms = _parse_room_number(listing.room_count or "")
        if rooms is not None:
            if min_rooms and rooms < float(min_rooms):
                return False, [f"oda_min_alti({rooms}<{min_rooms})"]
            if max_rooms and rooms > float(max_rooms):
                return False, [f"oda_max_ustu({rooms}>{max_rooms})"]
        else:
            reasons.append("oda_bilgisi_parse_edilemedi_elenmedi")

    if max_building_age is not None:
        building_age = _parse_building_age(listing.building_age or "")
        if building_age is not None and building_age > int(max_building_age):
            return False, [f"bina_yasi_max_ustu({building_age}>{max_building_age})"]
        if building_age is None:
            reasons.append("bina_yasi_parse_edilemedi_elenmedi")

    if is_furnished is not None and listing.is_furnished is not None:
        if listing.is_furnished != is_furnished:
            return False, [f"esyali_uyusmuyor({listing.is_furnished}!={is_furnished})"]
    elif is_furnished is not None and listing.is_furnished is None:
        reasons.append("esyali_bilgisi_yok_elenmedi")

    if seller_type and listing.seller_type:
        expected_seller = _normalize_text(str(seller_type))
        listing_seller = _normalize_text(listing.seller_type)

        if expected_seller == "sahibinden" and listing_seller != "sahibinden":
            return False, [f"satici_uyusmuyor({listing.seller_type}!=sahibinden)"]
        if expected_seller == "emlak" and listing_seller != "emlak":
            return False, [f"satici_uyusmuyor({listing.seller_type}!=emlak)"]
    elif seller_type and not listing.seller_type:
        reasons.append("satici_bilgisi_yok_elenmedi")

    if not reasons:
        reasons = ["uygun"]
    return True, reasons


def build_filter_debug_rows(listings: list[ListingModel], criteria: dict) -> list[dict]:
    rows: list[dict] = []
    for listing in listings:
        matched, reasons = _evaluate_listing(listing, criteria)
        rows.append(
            {
                "matched": matched,
                "reasons": reasons,
                "listing_id": listing.listing_id,
                "title": listing.title,
                "price": listing.price,
                "district": listing.district,
                "room_count": listing.room_count,
                "building_age": listing.building_age,
                "is_furnished": listing.is_furnished,
                "seller_type": listing.seller_type,
                "url": listing.url,
            }
        )
    return rows


def apply_basic_filters(listings: list[ListingModel], criteria: dict) -> list[ListingModel]:
    filtered = []
    for listing in listings:
        matched, _ = _evaluate_listing(listing, criteria)
        if matched:
            filtered.append(listing)
    return filtered
