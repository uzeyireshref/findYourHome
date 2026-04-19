import asyncio
import hashlib
import logging
import re
import unicodedata
from urllib.parse import urljoin

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

_TR_TRANSLATION = str.maketrans({
    "\u00c7": "c", "\u00e7": "c",
    "\u011e": "g", "\u011f": "g",
    "\u0130": "i", "\u0131": "i",
    "\u00d6": "o", "\u00f6": "o",
    "\u015e": "s", "\u015f": "s",
    "\u00dc": "u", "\u00fc": "u",
})


def _normalize_text(text: str) -> str:
    text = text or ""
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
    normalized = _normalize_text(text)
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

    if any(token in normalized for token in ("esyasiz", "esya yok", "mobilyasiz")):
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


def _emlakjet_category_segment(listing_type: str) -> str:
    if listing_type == "satilik":
        return "satilik-konut"
    return "kiralik-konut"


def _hepsiemlak_property_segment(property_type: str) -> str:
    mapping = {
        "daire": "daire",
        "villa": "villa",
        "residence": "residence",
        "mustakil-ev": "mustakil-ev",
        "yazlik": "yazlik",
    }
    return mapping.get(property_type, "daire")


def _absolute_url(base_url: str, href: str) -> str:
    return urljoin(base_url, href)


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


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

        header_el = a_tag.find(class_="list-view-header")
        title = header_el.get_text(strip=True) if header_el else ""

        price_el = a_tag.find(class_="list-view-price")
        price_text = price_el.get_text(strip=True) if price_el else "0"
        price = _parse_price(price_text)

        loc_el = a_tag.find(class_="list-view-location")
        location = loc_el.get_text(strip=True) if loc_el else ""

        size_el = a_tag.find(class_="list-view-size")
        size_text = size_el.get_text(separator=" | ", strip=True) if size_el else ""
        card_text = a_tag.get_text(separator=" | ", strip=True)

        room_match = re.search(r"(\d\+\d)", size_text)
        room_count = room_match.group(1) if room_match else ""
        if not room_count:
            room_match = re.search(r"(\d\+\d)", title)
            room_count = room_match.group(1) if room_match else ""

        detect_text = f"{title} {size_text} {card_text}"

        listings.append(ListingModel(
            listing_id=listing_id,
            title=title,
            price=price,
            district=location,
            room_count=room_count,
            url=full_url,
            is_furnished=_detect_furnished(detect_text),
            seller_type=_detect_seller_type(detect_text),
        ))

    return listings


async def _fetch_emlakjet(
    city: str,
    district: str,
    listing_type: str = "kiralik",
    property_type: str = "daire",
    min_price: float = None,
    max_price: float = None,
) -> list[ListingModel]:
    city_slug = _normalize_slug(city)
    category_segment = _emlakjet_category_segment(listing_type)

    if district:
        district_slug = _normalize_slug(district)
        url = f"https://www.emlakjet.com/{category_segment}/{city_slug}-{district_slug}/"
    else:
        url = f"https://www.emlakjet.com/{category_segment}/{city_slug}/"

    logging.info("Emlakjet taraniyor: %s", url)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
            response = await client.get(url, headers=HEADERS)
            if response.status_code != 200:
                logging.error("Emlakjet HTTP %s", response.status_code)
                return []
    except Exception as e:
        logging.error("Emlakjet istek hatasi: %s", e)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    listings = []
    seen_ids = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        if "/ilan/" not in href:
            continue

        match = re.search(r"-(\d+)$", href)
        if not match:
            continue

        listing_id = match.group(1)
        if listing_id in seen_ids:
            continue
        seen_ids.add(listing_id)

        card_text = a_tag.get_text(separator=" | ", strip=True)
        title_parts = card_text.split("|")
        title = title_parts[0].replace("YENI", "").replace("YEN\u0130", "").strip() if title_parts else ""

        room_match = re.search(r"(\d\+\d)", card_text)
        room_count = room_match.group(1) if room_match else ""

        loc_parts = re.findall(
            r"(\w+(?:\s+\w+)*)\s*-\s*(\w+(?:\s+\w+)*\s*(?:Mahallesi)?)",
            card_text,
        )
        location = f"{loc_parts[0][0]} - {loc_parts[0][1]}" if loc_parts else ""

        price = 0.0
        parent = a_tag.find_parent("div")
        parent_text = parent.get_text(" ", strip=True) if parent else ""
        if parent_text:
            price_match = re.search(r"([\d.]{3,})\s*(?:TL|\u20ba)", parent_text)
            if price_match:
                price = _parse_price(price_match.group(1))

        full_url = f"https://www.emlakjet.com{href}" if href.startswith("/") else href
        detect_text = f"{card_text} {parent_text}"

        listings.append(ListingModel(
            listing_id=f"ej_{listing_id}",
            title=title,
            price=price,
            district=location,
            room_count=room_count,
            url=full_url,
            is_furnished=_detect_furnished(detect_text),
            seller_type=_detect_seller_type(detect_text),
        ))

    logging.info("Emlakjet'ten %s ilan bulundu.", len(listings))
    return listings


def _fetch_hepsiemlak_sync(url: str) -> str:
    from curl_cffi import requests as cr

    session = cr.Session(impersonate="chrome")
    try:
        response = session.get(url, timeout=30)
        if response.status_code == 200:
            return response.text

        logging.error("Hepsiemlak HTTP %s", response.status_code)
        return ""
    except Exception as e:
        logging.error("Hepsiemlak istek hatasi: %s", e)
        return ""


async def _fetch_hepsiemlak(
    city: str,
    district: str,
    listing_type: str = "kiralik",
    property_type: str = "daire",
    min_price: float = None,
    max_price: float = None,
) -> list[ListingModel]:
    slug = _normalize_slug(district or city)
    property_segment = _hepsiemlak_property_segment(property_type)
    url = f"https://www.hepsiemlak.com/{slug}-{listing_type}/{property_segment}"

    logging.info("Hepsiemlak taraniyor: %s", url)

    try:
        html = await asyncio.to_thread(_fetch_hepsiemlak_sync, url)
    except Exception as e:
        logging.error("Hepsiemlak hatasi: %s", e)
        return []

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    listings = _parse_hepsiemlak_cards(soup, source_prefix="he", base_url="https://www.hepsiemlak.com")

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
        tasks.append(_fetch_emlakjet(city, district, listing_type, property_type, min_price, max_price))
        tasks.append(_fetch_hepsiemlak(city, district, listing_type, property_type, min_price, max_price))

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

    logging.info(
        "Toplam %s benzersiz ilan bulundu (Emlakjet + Hepsiemlak).",
        len(unique_listings),
    )
    return unique_listings
