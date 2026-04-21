import asyncio
import hashlib
import logging
import os
import random
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from scraper.normalizer import ListingModel


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
HEPSIEMLAK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.hepsiemlak.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
NO_PROXY = {"http": "", "https": "", "all": ""}
SOURCE_STATUS = {
    "hepsiemlak": {
        "state": "unknown",
        "message": "",
    }
}


def _set_hepsiemlak_status(state: str, message: str = "") -> None:
    SOURCE_STATUS["hepsiemlak"] = {"state": state, "message": message}


def get_source_status() -> dict:
    return SOURCE_STATUS.copy()


def _hepsiemlak_proxies() -> dict[str, str]:
    proxy = os.getenv("HEPSIEMLAK_PROXY", "").strip()
    if not proxy:
        return NO_PROXY
    return {"http": proxy, "https": proxy, "all": proxy}


def _brightdata_unlocker_config() -> tuple[str, str]:
    api_key = (
        os.getenv("BRIGHTDATA_API_KEY", "").strip()
        or os.getenv("BRIGHTDATA_UNLOCKER_API_KEY", "").strip()
    )
    zone = os.getenv("BRIGHTDATA_UNLOCKER_ZONE", "web_unlocker1").strip()
    return api_key, zone


def _hepsiemlak_blocked_message() -> str:
    proxy_configured = bool(os.getenv("HEPSIEMLAK_PROXY", "").strip())
    unlocker_configured = bool(_brightdata_unlocker_config()[0])
    if proxy_configured or unlocker_configured:
        return "Hepsiemlak origin IP engeli tespit edildi; proxy/unlocker ile tekrar denendi."
    return "Hepsiemlak origin IP engeli nedeniyle erisilemedi. HEPSIEMLAK_PROXY veya BRIGHTDATA_API_KEY ayarlayin."


def _is_hepsiemlak_html(content: str) -> bool:
    text = content or ""
    if len(text) < 5_000:
        return False

    lowered = text.lower()
    if "403 forbidden" in lowered or "access denied" in lowered:
        return False

    markers = (
        "__next_data__",
        "listing-card",
        "property-card",
        "search-results",
        "hepsiemlak",
    )
    return any(marker in lowered for marker in markers)


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 10) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _max_pages() -> int:
    return _env_int("SCRAPER_MAX_PAGES", 10, minimum=1, maximum=30)


def _detail_limit() -> int:
    return _env_int("SCRAPER_DETAIL_LIMIT", 300, minimum=10, maximum=1000)

_TR_TRANSLATION = str.maketrans({
    "\u00c7": "c", "\u00e7": "c",
    "\u011e": "g", "\u011f": "g",
    "\u0130": "i", "\u0131": "i",
    "\u00d6": "o", "\u00f6": "o",
    "\u015e": "s", "\u015f": "s",
    "\u00dc": "u", "\u00fc": "u",
})


def _normalize_text(text: str, append_repaired: bool = True) -> str:
    text = text or ""
    if append_repaired:
        try:
            repaired = text.encode("latin1").decode("utf-8")
            if repaired != text:
                text = f"{text} {repaired}"
        except UnicodeError:
            pass

    text = text.translate(_TR_TRANSLATION)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(text.lower().split())


def _normalize_slug(text: str) -> str:
    normalized = _normalize_text(text, append_repaired=False)
    normalized = re.sub(r"[^a-z0-9\s-]", "", normalized)
    return normalized.replace(" ", "-")


def _parse_price(text: str) -> float:
    cleaned = re.sub(r"[^\d,.]", "", text or "")
    if "." in cleaned and "," not in cleaned:
        cleaned = cleaned.replace(".", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _detect_furnished(text: str):
    normalized = _normalize_text(text)

    if any(token in normalized for token in ("esyali degil", "esyasiz", "esya yok", "mobilyasiz", "bos")):
        return False
    if any(token in normalized for token in ("esyali", "mobilyali", "full esya", "esya dahil", "ful esya")):
        return True
    return None


def _detect_seller_type(text: str):
    normalized = _normalize_text(text)

    if any(token in normalized for token in ("sahibinden", "sahibnden", "ev sahibinden")):
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


def _listing_type(criteria: dict) -> str:
    value = _normalize_text(str(criteria.get("listing_type") or "kiralik")).replace(" ", "-")
    allowed = {"kiralik", "satilik", "gunluk-kiralik"}
    return value if value in allowed else "kiralik"


def _property_type(criteria: dict) -> str:
    normalized = _normalize_text(str(criteria.get("property_type") or "daire")).replace(" ", "-")
    aliases = {
        "ev": "daire",
        "konut": "daire",
        "apartman": "daire",
        "rezidans": "residence",
        "mustakil": "mustakil-ev",
        "mustakil-ev": "mustakil-ev",
        "müstakil-ev": "mustakil-ev",
    }
    return aliases.get(normalized, normalized or "daire")


def _emlakjet_category_segment(listing_type: str, property_type: str) -> str:
    property_mapping = {
        "daire": "daire",
        "villa": "villa",
        "residence": "residence",
        "mustakil-ev": "mustakil-ev",
        "yazlik": "yazlik",
    }
    property_segment = property_mapping.get(property_type, "konut")

    if listing_type == "satilik":
        return f"satilik-{property_segment}"
    if listing_type == "gunluk-kiralik":
        return f"gunluk-kiralik-{property_segment}"
    return f"kiralik-{property_segment}"


def _hepsiemlak_property_segment(property_type: str) -> str:
    mapping = {
        "daire": "daire",
        "villa": "villa",
        "residence": "residence",
        "mustakil-ev": "mustakil-ev",
        "yazlik": "yazlik",
    }
    return mapping.get(property_type, "daire")


def _emlakjet_room_filter_values(criteria: dict | None) -> list[str]:
    criteria = criteria or {}
    min_rooms = criteria.get("min_rooms")
    max_rooms = criteria.get("max_rooms")
    if not min_rooms and not max_rooms:
        return []

    lower = int(float(min_rooms)) if min_rooms else 1
    upper = int(float(max_rooms)) if max_rooms else 6
    lower = max(1, lower)
    upper = max(lower, min(upper, 6))

    # Emlakjet URL filtrelerinde oda değerleri "2-1" => "2+1" formatında çalışıyor.
    return [
        f"{room}-{salon}"
        for room in range(lower, upper + 1)
        for salon in range(0, 3)
    ]


def _emlakjet_search_url(base_url: str, criteria: dict | None) -> str:
    criteria = criteria or {}
    params: list[tuple[str, str]] = []

    room_values = _emlakjet_room_filter_values(criteria)
    furnished = criteria.get("is_furnished")

    if furnished is True:
        params.append(("filtreler", "esya-durumu=esyali"))
        if room_values:
            params.append(("oda-sayisi", ",".join(room_values)))
    elif furnished is False:
        params.append(("filtreler", "esya-durumu=bos"))
        if room_values:
            params.append(("oda-sayisi", ",".join(room_values)))
    elif room_values:
        params.append(("filtreler", f"oda-sayisi={','.join(room_values)}"))

    min_price = criteria.get("min_price")
    max_price = criteria.get("max_price")
    if min_price:
        params.append(("min-fiyat", str(int(float(min_price)))))
    if max_price:
        params.append(("max-fiyat", str(int(float(max_price)))))

    return _add_query_params(base_url, params)


def _hepsiemlak_room_query_values(criteria: dict | None) -> str:
    values = _emlakjet_room_filter_values(criteria)
    return ",".join(value.replace("-", "+") for value in values)


def _hepsiemlak_search_url(base_url: str, criteria: dict | None) -> str:
    criteria = criteria or {}
    params: list[tuple[str, str]] = []

    min_price = criteria.get("min_price")
    max_price = criteria.get("max_price")
    if min_price:
        params.append(("priceMin", str(int(float(min_price)))))
    if max_price:
        params.append(("priceMax", str(int(float(max_price)))))

    room_values = _hepsiemlak_room_query_values(criteria)
    if room_values:
        params.append(("roomCount", room_values))

    if criteria.get("is_furnished") is True:
        params.append(("furnished", "true"))
    elif criteria.get("is_furnished") is False:
        params.append(("furnished", "false"))

    max_building_age = criteria.get("max_building_age")
    if max_building_age is not None:
        params.append(("buildingAgeMax", str(int(max_building_age))))

    return _add_query_params(base_url, params)


def _absolute_url(base_url: str, href: str) -> str:
    return urljoin(base_url, href)


def _with_page(url: str, page: int, page_param: str) -> str:
    if page <= 1:
        return url
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != page_param]
    query.append((page_param, str(page)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _add_query_params(url: str, params: list[tuple[str, str]]) -> str:
    if not params:
        return url

    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((key, value) for key, value in params if value not in (None, ""))
    # urlencode safe="=," prevents encoding `=` and `,` which Emlakjet requires for `filtreler`
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, safe="=,"), parts.fragment))


def _title_from_url(url: str) -> str:
    parts = [part for part in url.rstrip("/").split("/") if part]
    slug = parts[-1] if parts else ""
    if re.fullmatch(r"\d+(?:-\d+)?", slug) and len(parts) >= 2:
        slug = parts[-2]
    slug = re.sub(r"-\d+$", "", slug)
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _parse_room_count(text: str) -> str:
    text = text or ""
    match = re.search(r"(\d+(?:[,.]\d+)?\s*\+\s*\d+)", text)
    if match:
        return re.sub(r"\s+", "", match.group(1).replace(".", ","))
    match_single = re.search(r"(?i)\b(\d+)\s+oda(?:l[iı])?\b", text)
    if match_single:
        return f"{match_single.group(1)}+0"
    if "stüdyo" in text.lower() or "studyo" in text.lower():
        return "1+0"
    return ""


def _room_number(value: str):
    room_count = _parse_room_count(value)
    if not room_count:
        return None
    try:
        return float(room_count.split("+", 1)[0].replace(",", "."))
    except ValueError:
        return None


def _parse_building_age(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    if any(token in normalized for token in ("sifir", "yeni", "new")) or re.fullmatch(r"0(?:\s*-\s*5)?", normalized):
        return "0"
    match = re.search(r"\d+", normalized)
    return match.group(0) if match else ""


def _detail_tokens(text: str) -> list[str]:
    return [token.strip() for token in (text or "").split("|") if token.strip()]


def _value_after_any_label(text: str, labels: tuple[str, ...]) -> str:
    tokens = _detail_tokens(text)
    normalized_labels = {_normalize_text(label) for label in labels}

    for index, token in enumerate(tokens[:-1]):
        normalized_token = _normalize_text(token).rstrip(":")
        if normalized_token in normalized_labels:
            return tokens[index + 1].strip()

    # Some pages render specs as "Label: value" instead of adjacent tokens.
    for token in tokens:
        normalized_token = _normalize_text(token)
        for label in normalized_labels:
            if normalized_token.startswith(f"{label}:"):
                return token.split(":", 1)[1].strip()

    return ""


def _safe_detail_value(text: str, labels: tuple[str, ...]) -> str:
    value = _value_after_any_label(text, labels)
    normalized = _normalize_text(value)
    if normalized in {"", "-", "belirtilmemis", "belirtilmedi", "yok"}:
        return ""
    return value


ROOM_LABELS = ("Oda Sayısı", "Oda SayÄ±sÄ±", "Oda Sayisi")
FURNITURE_LABELS = ("Eşya Durumu", "EÅŸya Durumu", "Esya Durumu")
BUILDING_AGE_LABELS = ("Bina Yaşı", "Binanın Yaşı", "Bina YaÅŸÄ±", "BinanÄ±n YaÅŸÄ±", "Bina Yasi", "Binanin Yasi")
SELLER_LABELS = ("Kimden", "İlan Sahibi", "Ilan Sahibi")
AUTHORIZED_OFFICE_LABELS = ("Yetkili Ofis",)

# Keep correct Unicode labels in addition to older mojibake variants above.
ROOM_LABELS = ("Oda Sayısı", *ROOM_LABELS)
FURNITURE_LABELS = ("Eşya Durumu", *FURNITURE_LABELS)
BUILDING_AGE_LABELS = ("Bina Yaşı", "Binanın Yaşı", *BUILDING_AGE_LABELS)
SELLER_LABELS = ("İlan Sahibi", *SELLER_LABELS)


def _value_after_label(text: str, label: str) -> str:
    return _value_after_any_label(text, (label,))


def _criteria_maybe_matches(listing: ListingModel, criteria: dict | None) -> bool:
    criteria = criteria or {}

    min_price = criteria.get("min_price")
    max_price = criteria.get("max_price")
    if listing.price:
        if min_price and listing.price < min_price:
            return False
        if max_price and listing.price > max_price:
            return False

    district = criteria.get("district")
    if district and listing.district:
        allowed_districts = [_normalize_text(d.strip()) for d in str(district).split(",") if d.strip()]
        listing_district = _normalize_text(listing.district)
        if allowed_districts and not any(d in listing_district for d in allowed_districts):
            return False

    room_number = _room_number(listing.room_count or "")
    if room_number is not None:
        min_rooms = criteria.get("min_rooms")
        max_rooms = criteria.get("max_rooms")
        if min_rooms and room_number < float(min_rooms):
            return False
        if max_rooms and room_number > float(max_rooms):
            return False

    if criteria.get("is_furnished") is not None and listing.is_furnished is not None:
        if listing.is_furnished != criteria["is_furnished"]:
            return False

    expected_seller = _normalize_text(str(criteria.get("seller_type") or ""))
    if expected_seller and listing.seller_type:
        listing_seller = _normalize_text(listing.seller_type)
        if expected_seller == "sahibinden" and listing_seller != "sahibinden":
            return False
        if expected_seller == "emlak" and listing_seller != "emlak":
            return False

    max_building_age = criteria.get("max_building_age")
    if max_building_age is not None and listing.building_age:
        building_age = _parse_building_age(listing.building_age)
        if building_age and int(building_age) > int(max_building_age):
            return False

    return True


def _needs_detail(listing: ListingModel, criteria: dict | None) -> bool:
    criteria = criteria or {}
    return (
        ((criteria.get("min_price") or criteria.get("max_price")) and not listing.price)
        or (criteria.get("district") and not listing.district)
        or ((criteria.get("min_rooms") or criteria.get("max_rooms")) and not listing.room_count)
        or (criteria.get("is_furnished") is not None and listing.is_furnished is None)
        or bool(criteria.get("seller_type") and not listing.seller_type)
        or bool(criteria.get("max_building_age") is not None)
    )


def _apply_emlakjet_server_assertions(listing: ListingModel, criteria: dict | None) -> ListingModel:
    criteria = criteria or {}
    updates = {}

    # Emlakjet supports these filters directly in the search URL. If the card
    # does not expose the same field, keep the server-side constraint instead
    # of dropping otherwise valid listings before detail enrichment.
    if criteria.get("is_furnished") is not None and listing.is_furnished is None:
        updates["is_furnished"] = criteria["is_furnished"]

    seller_type = _normalize_text(str(criteria.get("seller_type") or ""))
    if seller_type in {"sahibinden", "emlak"} and not listing.seller_type:
        updates["seller_type"] = seller_type

    return listing.model_copy(update=updates) if updates else listing


def _parse_hepsiemlak_cards(
    soup: BeautifulSoup,
    source_prefix: str = "he",
    base_url: str = "https://www.hepsiemlak.com",
) -> list[ListingModel]:
    listings = []

    for a_tag in soup.find_all("a", class_="listingView__card-link"):
        href = a_tag.get("href", "")
        if not href:
            continue

        id_match = re.search(r"/(\d+[-/]\d+)", href)
        raw_id = id_match.group(1).replace("/", "-") if id_match else href.split("/")[-1]
        full_url = _absolute_url(base_url, href)
        listing_id = f"{source_prefix}_{raw_id}" if raw_id else _stable_id(source_prefix, full_url)

        card_root = a_tag.find_parent(["li", "div"]) or a_tag
        header_el = card_root.find(class_="list-view-header") or a_tag.find(class_="list-view-header")
        title = header_el.get_text(strip=True) if header_el else ""

        price_el = card_root.find(class_="list-view-price") or a_tag.find(class_="list-view-price")
        price_text = price_el.get_text(strip=True) if price_el else "0"
        price = _parse_price(price_text)

        loc_el = card_root.find(class_="list-view-location") or a_tag.find(class_="list-view-location")
        location = loc_el.get_text(strip=True) if loc_el else ""

        size_el = card_root.find(class_="list-view-size") or a_tag.find(class_="list-view-size")
        size_text = size_el.get_text(separator=" | ", strip=True) if size_el else ""
        card_text = card_root.get_text(" | ", strip=True)
        if not title:
            title = _title_from_url(full_url)

        room_count = _parse_room_count(size_text) or _parse_room_count(title)
        listings.append(ListingModel(
            listing_id=listing_id,
            title=title,
            price=price,
            district=location,
            room_count=room_count,
            url=full_url,
            is_furnished=_detect_furnished(card_text),
            seller_type=_detect_seller_type(card_text),
        ))

    return listings


def _extract_json_object_after_marker(text: str, marker: str):
    # Emlakjet renders listing data inside escaped React flight chunks.
    normalized = (
        (text or "")
        .replace('\\"', '"')
        .replace("\\u0026", "&")
        .replace("\\/", "/")
    )
    marker_index = normalized.find(marker)
    if marker_index == -1:
        return None

    start = normalized.find("{", marker_index)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(normalized[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    import json

                    return json.loads(normalized[start:index + 1])
                except ValueError:
                    return None

    return None


def _quick_info_value(record: dict, key: str, label: str = "") -> str:
    normalized_label = _normalize_text(label)
    for item in record.get("quickInfos") or []:
        if item.get("key") == key:
            return str(item.get("value") or "")
        if normalized_label and _normalize_text(str(item.get("name") or "")) == normalized_label:
            return str(item.get("value") or "")
    return ""


def _emlakjet_seller_type(record: dict):
    owner = record.get("owner") or {}
    owner_type = _normalize_text(str(owner.get("type") or ""))
    if owner_type in {"personal", "individual", "sahibinden"}:
        return "sahibinden"
    if owner_type in {"corporate", "company", "office", "emlak"}:
        return "emlak"
    return _detect_seller_type(str(owner.get("name") or ""))


def _emlakjet_furnished_from_record(record: dict):
    furniture_value = _quick_info_value(record, "furnished", "Esya Durumu")
    if not furniture_value:
        furniture_value = _quick_info_value(record, "furniture_status", "Esya Durumu")
    if furniture_value:
        return _detect_furnished(furniture_value)

    haystack = " ".join(
        str(value or "")
        for value in (
            record.get("title"),
            record.get("description"),
            record.get("descriptionText"),
        )
    )
    return _detect_furnished(haystack)


def _fetch_unlocker_html_sync(url: str, client: httpx.Client | None = None) -> str:
    api_key, zone = _brightdata_unlocker_config()
    if not api_key:
        return ""

    close_client = client is None
    try:
        if client is None:
            client = httpx.Client(timeout=60.0, trust_env=False)
        response = client.post(
            "https://api.brightdata.com/request",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "zone": zone,
                "url": url,
                "format": "raw",
            },
        )
        if response.status_code == 200 and response.text:
            return response.text
        logging.warning(
            "Bright Data detay HTML alinamadi: status=%s bytes=%s url=%s",
            response.status_code,
            len(response.text or ""),
            url,
        )
    except Exception as e:
        logging.warning("Bright Data detay istek hatasi (%s): %s", url, e)
    finally:
        if close_client and client is not None:
            client.close()

    return ""


def _parse_emlakjet_listing_card(html: str) -> list[ListingModel]:
    listing_card = _extract_json_object_after_marker(html, '"listingCard":')
    if not listing_card:
        return []

    listings = []
    for record in listing_card.get("records") or []:
        listing_id = str(record.get("id") or "")
        href = str(record.get("url") or "")
        if not listing_id or not href:
            continue

        price_detail = record.get("priceDetail") or {}
        price = (
            price_detail.get("tlPrice")
            or price_detail.get("price")
            or price_detail.get("firstPrice")
            or 0
        )
        try:
            price = float(price)
        except (TypeError, ValueError):
            price = 0.0

        room_count = (
            _parse_room_count(str(record.get("roomCountName") or ""))
            or _parse_room_count(_quick_info_value(record, "room_count", "Oda Sayısı"))
            or _parse_room_count(str(record.get("title") or ""))
        )

        title = str(record.get("title") or "").strip()
        full_url = _absolute_url("https://www.emlakjet.com", href)
        owner = record.get("owner") if isinstance(record.get("owner"), dict) else {}

        listings.append(
            ListingModel(
                listing_id=f"ej_{listing_id}",
                title=title or _title_from_url(full_url),
                price=price,
                district=str(record.get("locationSummary") or ""),
                room_count=room_count,
                url=full_url,
                is_furnished=_emlakjet_furnished_from_record(record),
                seller_type=_emlakjet_seller_type(record),
            )
        )

    return listings


async def _enrich_emlakjet_single(client: httpx.AsyncClient, listing: ListingModel, criteria: dict | None) -> ListingModel:
    if not _needs_detail(listing, criteria):
        return listing

    try:
        response = await client.get(listing.url)
        if response.status_code != 200:
            logging.warning("Emlakjet detay HTTP %s (%s)", response.status_code, listing.url)
            return listing
    except Exception as e:
        logging.warning("Emlakjet detay istek hatasi (%s): %s", listing.url, e)
        return listing

    soup = BeautifulSoup(response.text, "html.parser")
    detail_text = soup.get_text(" | ", strip=True)

    title = listing.title
    title_candidate = soup.find("h1")
    if title_candidate:
        title = title_candidate.get_text(" ", strip=True) or title

    room_value = _safe_detail_value(detail_text, ROOM_LABELS)
    room_count = _parse_room_count(room_value) or listing.room_count

    furniture_value = _safe_detail_value(detail_text, FURNITURE_LABELS)
    is_furnished = listing.is_furnished
    if furniture_value:
        is_furnished = _detect_furnished(furniture_value)
    elif (criteria or {}).get("is_furnished") is not None:
        is_furnished = None

    building_age = listing.building_age
    building_age_value = _safe_detail_value(detail_text, BUILDING_AGE_LABELS)
    if building_age_value:
        building_age = _parse_building_age(building_age_value) or building_age

    seller_type = listing.seller_type
    seller_value = _safe_detail_value(detail_text, SELLER_LABELS)
    if seller_value:
        seller_type = _detect_seller_type(seller_value) or seller_type

    price = listing.price
    if not price:
        price_match = re.search(r"([\d.]{3,})\s*(?:TL|₺)", detail_text)
        if price_match:
            price = _parse_price(price_match.group(1))

    return listing.model_copy(
        update={
            "title": title,
            "price": price,
            "room_count": room_count,
            "building_age": building_age,
            "is_furnished": is_furnished,
            "seller_type": seller_type,
            "description": detail_text[:1000],
        }
    )


async def _enrich_emlakjet_details_async(listings: list[ListingModel], criteria: dict | None = None) -> list[ListingModel]:
    detail_limit = _detail_limit()
    sem = asyncio.Semaphore(5)
    
    async def bounded_fetch(client_, listing_):
        async with sem:
            return await _enrich_emlakjet_single(client_, listing_, criteria)

    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0, headers=HEADERS, trust_env=False) as client:
        tasks = []
        for index, listing in enumerate(listings):
            if index >= detail_limit:
                tasks.append(asyncio.sleep(0, result=listing))
            else:
                tasks.append(bounded_fetch(client, listing))
                
        if tasks:
            enriched = await asyncio.gather(*tasks)
            return list(enriched)
        return []

def _enrich_hepsiemlak_single_sync(listing: ListingModel, criteria: dict | None, session, proxies, unlocker_client) -> ListingModel:
    if not _needs_detail(listing, criteria):
        return listing

    try:
        html = _fetch_unlocker_html_sync(listing.url, unlocker_client)
        if not html:
            response = session.get(listing.url, headers=HEPSIEMLAK_HEADERS, proxies=proxies, timeout=15)
            if response.status_code != 200:
                logging.warning("Hepsiemlak detay HTTP %s (%s)", response.status_code, listing.url)
                return listing
            html = response.text
        if not html:
            return listing
    except Exception as e:
        logging.warning("Hepsiemlak detay istek hatasi (%s): %s", listing.url, e)
        return listing

    soup = BeautifulSoup(html, "html.parser")
    detail_text = soup.get_text(" | ", strip=True)

    title = listing.title
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True) or title
    elif _normalize_text(title) in {"daire", "residence", "villa", "mustakil ev"}:
        title_candidate = detail_text.split("|", 1)[0].strip()
        title = re.sub(r"\s+\d{3,}-\d+\s*$", "", title_candidate).strip() or title

    room_value = _safe_detail_value(detail_text, ROOM_LABELS)
    room_count = _parse_room_count(room_value) or listing.room_count

    is_furnished = listing.is_furnished
    furniture_value = _safe_detail_value(detail_text, FURNITURE_LABELS)
    if furniture_value:
        is_furnished = _detect_furnished(furniture_value)
    elif (criteria or {}).get("is_furnished") is not None:
        is_furnished = None

    building_age = listing.building_age
    building_age_value = _safe_detail_value(detail_text, BUILDING_AGE_LABELS)
    if building_age_value:
        building_age = _parse_building_age(building_age_value) or building_age

    seller_type = listing.seller_type
    seller_value = _safe_detail_value(detail_text, SELLER_LABELS)
    if seller_value:
        seller_type = _detect_seller_type(seller_value) or seller_type
    authorized_office = _safe_detail_value(detail_text, AUTHORIZED_OFFICE_LABELS)
    if _normalize_text(authorized_office) == "evet":
        seller_type = "emlak"

    return listing.model_copy(
        update={
            "title": title,
            "room_count": room_count,
            "building_age": building_age,
            "is_furnished": is_furnished,
            "seller_type": seller_type,
            "description": detail_text[:1000],
        }
    )


async def _enrich_hepsiemlak_details_async(listings: list[ListingModel], criteria: dict | None = None) -> list[ListingModel]:
    from curl_cffi import requests as cr

    session = cr.Session(impersonate="chrome", trust_env=False)
    proxies = _hepsiemlak_proxies()
    detail_limit = _detail_limit()
    
    unlocker_client = None
    if _brightdata_unlocker_config()[0]:
        unlocker_client = httpx.Client(timeout=60.0, trust_env=False)

    sem = asyncio.Semaphore(5)
    
    async def bounded_fetch(listing_):
        async with sem:
            return await asyncio.to_thread(_enrich_hepsiemlak_single_sync, listing_, criteria, session, proxies, unlocker_client)

    try:
        tasks = []
        for index, listing in enumerate(listings):
            if index >= detail_limit:
                tasks.append(asyncio.sleep(0, result=listing))
            else:
                tasks.append(bounded_fetch(listing))
                
        if tasks:
            enriched = await asyncio.gather(*tasks)
            return list(enriched)
        return []
    finally:
        if unlocker_client is not None:
            unlocker_client.close()

async def _fetch_emlakjet(
    city: str,
    district: str,
    listing_type: str = "kiralik",
    property_type: str = "daire",
    min_price: float = None,
    max_price: float = None,
    criteria: dict | None = None,
) -> list[ListingModel]:
    city_slug = _normalize_slug(city)
    category_segment = _emlakjet_category_segment(listing_type, property_type)

    if district:
        district_slug = _normalize_slug(district)
        base_url = f"https://www.emlakjet.com/{category_segment}/{city_slug}-{district_slug}"
    else:
        base_url = f"https://www.emlakjet.com/{category_segment}/{city_slug}"

    seller_type = _normalize_text(str((criteria or {}).get("seller_type") or ""))
    if seller_type == "sahibinden":
        base_url = f"{base_url}/sahibinden"
    elif seller_type == "emlak":
        base_url = f"{base_url}/emlakcidan"

    base_url = _emlakjet_search_url(base_url, criteria)
    logging.info("Emlakjet taraniyor: %s", base_url)

    listings = []
    seen_ids = set()
    max_pages = _max_pages()

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0, trust_env=False) as client:
            for page in range(1, max_pages + 1):
                url = _with_page(base_url, page, "sayfa")
                response = await client.get(url, headers=HEADERS)
                if response.status_code != 200:
                    logging.warning("Emlakjet HTTP %s (%s)", response.status_code, url)
                    break

                page_listings = _parse_emlakjet_listing_card(response.text)
                if not page_listings:
                    soup = BeautifulSoup(response.text, "html.parser")
                    page_listings = []
                    for a_tag in soup.find_all("a", href=True):
                        href = a_tag.get("href", "")
                        if "/ilan/" not in href:
                            continue

                        match = re.search(r"-(\d+)$", href)
                        if not match:
                            continue

                        listing_id = match.group(1)
                        card_text = a_tag.get_text(separator=" | ", strip=True)
                        parent = a_tag.find_parent("div")
                        parent_text = parent.get_text(" ", strip=True) if parent else ""
                        price_match = re.search(r"([\d.]{3,})\s*(?:TL|\u20ba)", parent_text)
                        full_url = _absolute_url("https://www.emlakjet.com", href)

                        page_listings.append(
                            ListingModel(
                                listing_id=f"ej_{listing_id}",
                                title=_title_from_url(full_url),
                                price=_parse_price(price_match.group(1)) if price_match else 0.0,
                                district="",
                                room_count=_parse_room_count(card_text),
                                url=full_url,
                                is_furnished=None,
                                seller_type=_detect_seller_type(f"{card_text} {parent_text}"),
                            )
                        )

                new_count = 0
                for listing in page_listings:
                    listing = _apply_emlakjet_server_assertions(listing, criteria)
                    if listing.listing_id in seen_ids:
                        continue
                    seen_ids.add(listing.listing_id)
                    if not _criteria_maybe_matches(listing, criteria):
                        continue
                    listings.append(listing)
                    new_count += 1

                logging.info(
                    "Emlakjet sayfa %s: %s ilan parse edildi, %s aday eklendi.",
                    page,
                    len(page_listings),
                    new_count,
                )
                if not page_listings:
                    break
    except Exception as e:
        logging.error("Emlakjet istek hatasi: %s", e)
        return []

    listings = await _enrich_emlakjet_details_async(listings, criteria)
    logging.info("Emlakjet'ten %s ilan bulundu.", len(listings))
    return listings


def _fetch_hepsiemlak_sync(urls: list[str]) -> str:
    from curl_cffi import requests as cr

    session = cr.Session(impersonate="chrome", trust_env=False)
    proxies = _hepsiemlak_proxies()
    header_variants = [
        HEPSIEMLAK_HEADERS,
        {**HEPSIEMLAK_HEADERS, "Sec-Fetch-Site": "none"},
        HEADERS,
    ]

    try:
        warmup = session.get(
            "https://www.hepsiemlak.com/",
            headers=HEPSIEMLAK_HEADERS,
            proxies=proxies,
            timeout=15,
        )
        logging.info(
            "Hepsiemlak warmup: status=%s bytes=%s",
            warmup.status_code,
            len(warmup.text or ""),
        )
    except Exception as e:
        logging.warning("Hepsiemlak warmup hatasi: %s", e)

    for url in urls:
        for headers in header_variants:
            for attempt in range(1, 4):
                try:
                    response = session.get(url, headers=headers, proxies=proxies, timeout=20)
                    body_size = len(response.text or "")
                    logging.info(
                        "Hepsiemlak curl denemesi: status=%s bytes=%s attempt=%s url=%s",
                        response.status_code,
                        body_size,
                        attempt,
                        url,
                    )
                    if response.status_code == 200 and _is_hepsiemlak_html(response.text):
                        _set_hepsiemlak_status("ok")
                        logging.info("Hepsiemlak cevap verdi: %s", url)
                        return response.text

                    if response.status_code == 403:
                        _set_hepsiemlak_status(
                            "blocked",
                            _hepsiemlak_blocked_message(),
                        )

                    logging.warning("Hepsiemlak HTTP %s (%s)", response.status_code, url)
                    break
                except Exception as e:
                    logging.warning("Hepsiemlak istek hatasi (%s/%s): %s", attempt, url, e)

    return ""


async def _fetch_hepsiemlak_httpx_fallback(urls: list[str]) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=25.0, trust_env=False) as client:
        for url in urls:
            try:
                response = await client.get(url, headers=HEADERS)
                body_size = len(response.text or "")
                logging.info(
                    "Hepsiemlak httpx fallback: status=%s bytes=%s url=%s",
                    response.status_code,
                    body_size,
                    url,
                )
                if response.status_code == 200 and body_size > 50_000:
                    if not _is_hepsiemlak_html(response.text):
                        continue
                    _set_hepsiemlak_status("ok")
                    return response.text
                if response.status_code == 403:
                    _set_hepsiemlak_status(
                        "blocked",
                        _hepsiemlak_blocked_message(),
                    )
            except Exception as e:
                logging.warning("Hepsiemlak httpx fallback hatasi (%s): %s", url, e)

    return ""


async def _fetch_hepsiemlak_unlocker_api(urls: list[str]) -> str:
    api_key, zone = _brightdata_unlocker_config()
    if not api_key:
        return ""

    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        for url in urls:
            try:
                response = await client.post(
                    "https://api.brightdata.com/request",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "zone": zone,
                        "url": url,
                        "format": "raw",
                    },
                )
                body_size = len(response.text or "")
                logging.info(
                    "Hepsiemlak Bright Data Unlocker: status=%s bytes=%s zone=%s url=%s",
                    response.status_code,
                    body_size,
                    zone,
                    url,
                )

                if response.status_code == 200 and _is_hepsiemlak_html(response.text):
                    _set_hepsiemlak_status("ok")
                    return response.text

                if response.status_code in {401, 403}:
                    _set_hepsiemlak_status(
                        "unlocker_auth_error",
                        "Bright Data Unlocker API key veya zone yetkisi reddedildi.",
                    )
                elif response.status_code == 429:
                    _set_hepsiemlak_status(
                        "unlocker_rate_limited",
                        "Bright Data Unlocker limit/rate-limit verdi.",
                    )
            except Exception as e:
                logging.warning("Hepsiemlak Bright Data Unlocker hatasi (%s): %s", url, e)

    return ""


def _fetch_hepsiemlak_sync_pages(urls: list[str]) -> list[str]:
    from curl_cffi import requests as cr

    session = cr.Session(impersonate="chrome", trust_env=False)
    proxies = _hepsiemlak_proxies()
    pages = []

    try:
        session.get(
            "https://www.hepsiemlak.com/",
            headers=HEPSIEMLAK_HEADERS,
            proxies=proxies,
            timeout=15,
        )
    except Exception as e:
        logging.warning("Hepsiemlak warmup hatasi: %s", e)

    for url in urls:
        try:
            response = session.get(url, headers=HEPSIEMLAK_HEADERS, proxies=proxies, timeout=20)
            body_size = len(response.text or "")
            logging.info(
                "Hepsiemlak sayfa denemesi: status=%s bytes=%s url=%s",
                response.status_code,
                body_size,
                url,
            )
            if response.status_code == 200 and _is_hepsiemlak_html(response.text):
                pages.append(response.text)
            elif response.status_code == 403:
                _set_hepsiemlak_status(
                    "blocked",
                    _hepsiemlak_blocked_message(),
                )
        except Exception as e:
            logging.warning("Hepsiemlak sayfa istek hatasi (%s): %s", url, e)

    return pages


async def _fetch_hepsiemlak_httpx_fallback_pages(urls: list[str]) -> list[str]:
    pages = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=25.0, trust_env=False) as client:
        for url in urls:
            try:
                response = await client.get(url, headers=HEADERS)
                body_size = len(response.text or "")
                logging.info(
                    "Hepsiemlak httpx sayfa fallback: status=%s bytes=%s url=%s",
                    response.status_code,
                    body_size,
                    url,
                )
                if response.status_code == 200 and _is_hepsiemlak_html(response.text):
                    pages.append(response.text)
                elif response.status_code == 403:
                    _set_hepsiemlak_status(
                        "blocked",
                        _hepsiemlak_blocked_message(),
                    )
            except Exception as e:
                logging.warning("Hepsiemlak httpx sayfa fallback hatasi (%s): %s", url, e)

    return pages


async def _fetch_hepsiemlak_unlocker_api_pages(urls: list[str]) -> list[str]:
    api_key, zone = _brightdata_unlocker_config()
    if not api_key:
        return []

    pages = []
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        for url in urls:
            try:
                response = await client.post(
                    "https://api.brightdata.com/request",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "zone": zone,
                        "url": url,
                        "format": "raw",
                    },
                )
                body_size = len(response.text or "")
                logging.info(
                    "Hepsiemlak Unlocker sayfa: status=%s bytes=%s zone=%s url=%s",
                    response.status_code,
                    body_size,
                    zone,
                    url,
                )
                if response.status_code == 200 and _is_hepsiemlak_html(response.text):
                    pages.append(response.text)
                elif response.status_code in {401, 403}:
                    _set_hepsiemlak_status(
                        "unlocker_auth_error",
                        "Bright Data Unlocker API key veya zone yetkisi reddedildi.",
                    )
                elif response.status_code == 429:
                    _set_hepsiemlak_status(
                        "unlocker_rate_limited",
                        "Bright Data Unlocker limit/rate-limit verdi.",
                    )
            except Exception as e:
                logging.warning("Hepsiemlak Unlocker sayfa hatasi (%s): %s", url, e)

    return pages


async def _fetch_hepsiemlak(
    city: str,
    district: str,
    listing_type: str = "kiralik",
    property_type: str = "daire",
    min_price: float = None,
    max_price: float = None,
    criteria: dict | None = None,
) -> list[ListingModel]:
    _set_hepsiemlak_status("checking")
    city_slug = _normalize_slug(city)
    district_slug = _normalize_slug(district) if district else ""
    location_slug = f"{city_slug}-{district_slug}" if district_slug else city_slug
    legacy_slug = district_slug or city_slug
    property_segment = _hepsiemlak_property_segment(property_type)
    seller_type = _normalize_text(str((criteria or {}).get("seller_type") or ""))
    seller_suffix = "-sahibinden" if seller_type == "sahibinden" else ""
    
    location_candidates = [
        location_slug,   # city-district
        district_slug,   # district
        legacy_slug,     # legacy behavior
        city_slug,       # city
    ]
    location_candidates = [value for value in dict.fromkeys(location_candidates) if value]

    base_urls = []
    for candidate in location_candidates:
        base_urls.extend([
            f"https://www.hepsiemlak.com/{candidate}-{listing_type}{seller_suffix}/{property_segment}",
            f"https://www.hepsiemlak.com/{candidate}-{listing_type}{seller_suffix}",
        ])
        if seller_suffix:
            base_urls.extend([
                f"https://www.hepsiemlak.com/{candidate}-{listing_type}/{property_segment}",
                f"https://www.hepsiemlak.com/{candidate}-{listing_type}",
            ])
    base_urls = list(dict.fromkeys(_hepsiemlak_search_url(url, criteria) for url in base_urls))
    urls = [
        _with_page(base_url, page, "page")
        for base_url in base_urls
        for page in range(1, _max_pages() + 1)
    ]

    logging.info("Hepsiemlak taraniyor: %s", ", ".join(urls))

    pages = await _fetch_hepsiemlak_unlocker_api_pages(urls)

    if pages:
        logging.info("Hepsiemlak Bright Data Unlocker ile %s sayfa alindi.", len(pages))
    else:
        logging.info("Hepsiemlak Bright Data Unlocker kullanilamadi/bos dondu; native proxy deneniyor.")

    try:
        if not pages:
            pages = await asyncio.to_thread(_fetch_hepsiemlak_sync_pages, urls)
    except Exception as e:
        logging.error("Hepsiemlak hatasi: %s", e)
        pages = []

    if not pages:
        logging.warning("Hepsiemlak curl ile bos dondu; httpx fallback deneniyor.")
        pages = await _fetch_hepsiemlak_httpx_fallback_pages(urls)

    if not pages:
        if SOURCE_STATUS["hepsiemlak"]["state"] != "blocked":
            _set_hepsiemlak_status("unavailable", "Hepsiemlak HTML alinamadi.")
        logging.error("Hepsiemlak HTML alinamadi: %s", ", ".join(urls))
        return []

    listings = []
    seen_ids = set()
    for html in pages:
        soup = BeautifulSoup(html, "html.parser")
        page_listings = _parse_hepsiemlak_cards(soup, source_prefix="he", base_url="https://www.hepsiemlak.com")
        for listing in page_listings:
            if listing.listing_id in seen_ids:
                continue
            seen_ids.add(listing.listing_id)
            if not _criteria_maybe_matches(listing, criteria):
                continue
            listings.append(listing)

    logging.info("Hepsiemlak kart parse sonucu: %s", len(listings))
    if not listings:
        _set_hepsiemlak_status("parse_empty", "Hepsiemlak sayfasi acildi ama ilan karti parse edilemedi.")
    listings = await _enrich_hepsiemlak_details_async(listings, criteria)

    if listings:
        _set_hepsiemlak_status("ok")
    logging.info("Hepsiemlak'tan %s ilan bulundu.", len(listings))
    return listings


async def fetch_listings(criteria: dict) -> list[ListingModel]:
    city = criteria.get("city", "istanbul") or "istanbul"
    district_raw = criteria.get("district", "") or ""
    districts = [d.strip() for d in district_raw.split(",") if d.strip()]
    if not districts:
        districts = [""]

    min_price = criteria.get("min_price")
    max_price = criteria.get("max_price")
    listing_type = _listing_type(criteria)
    property_type = _property_type(criteria)

    tasks = []
    for district in districts:
        tasks.append(_fetch_emlakjet(city, district, listing_type, property_type, min_price, max_price, criteria))
        tasks.append(_fetch_hepsiemlak(city, district, listing_type, property_type, min_price, max_price, criteria))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_listings = []
    for result in results:
        if isinstance(result, list):
            all_listings.extend(result)
        elif isinstance(result, Exception):
            logging.error("Scraper hatasi: %s", result)

    seen_urls = set()
    unique_listings = []
    for listing in all_listings:
        if listing.url not in seen_urls:
            seen_urls.add(listing.url)
            unique_listings.append(listing)

    random.shuffle(unique_listings)

    logging.info(
        "Toplam %s benzersiz ilan bulundu (Emlakjet + Hepsiemlak).",
        len(unique_listings),
    )
    return unique_listings
