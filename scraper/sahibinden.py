import asyncio
import hashlib
import logging
import os
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

    if any(token in normalized for token in ("esyali degil", "esyasiz", "esya yok", "mobilyasiz")):
        return False
    if any(token in normalized for token in ("esyali", "mobilyali", "full esya", "esya dahil")):
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
        card_text = card_root.get_text(separator=" | ", strip=True)

        if not title:
            title = _title_from_url(full_url)

        room_match = re.search(r"(\d+(?:,\d+)?\s*\+\s*\d)", size_text)
        room_count = room_match.group(1) if room_match else ""
        if not room_count:
            room_match = re.search(r"(\d+(?:,\d+)?\s*\+\s*\d)", f"{title} {card_text}")
            room_count = room_match.group(1) if room_match else ""
        room_count = re.sub(r"\s+", "", room_count)

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


def _value_after_label(text: str, label: str) -> str:
    match = re.search(
        rf"{re.escape(label)}\s*\|?\s*([^|]+)",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def _enrich_hepsiemlak_details_sync(listings: list[ListingModel]) -> list[ListingModel]:
    from curl_cffi import requests as cr

    session = cr.Session(impersonate="chrome", trust_env=False)
    proxies = _hepsiemlak_proxies()
    enriched = []

    for listing in listings[:30]:
        needs_detail = not listing.room_count or listing.is_furnished is None or listing.seller_type is None
        if not needs_detail:
            enriched.append(listing)
            continue

        try:
            response = session.get(listing.url, headers=HEPSIEMLAK_HEADERS, proxies=proxies, timeout=15)
            if response.status_code != 200:
                logging.warning("Hepsiemlak detay HTTP %s (%s)", response.status_code, listing.url)
                enriched.append(listing)
                continue
        except Exception as e:
            logging.warning("Hepsiemlak detay istek hatasi (%s): %s", listing.url, e)
            enriched.append(listing)
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        detail_text = soup.get_text(" | ", strip=True)

        title = listing.title
        if _normalize_text(title) in {"daire", "residence", "villa", "mustakil ev"}:
            title_candidate = detail_text.split("|", 1)[0].strip()
            title = re.sub(r"\s+\d{3,}-\d+\s*$", "", title_candidate).strip() or title

        room_count = listing.room_count
        if not room_count:
            room_value = _value_after_label(detail_text, "Oda Sayısı")
            room_match = re.search(r"(\d+(?:,\d+)?\s*\+\s*\d)", room_value or detail_text)
            room_count = re.sub(r"\s+", "", room_match.group(1)) if room_match else room_count

        is_furnished = listing.is_furnished
        if is_furnished is None:
            furniture_value = _value_after_label(detail_text, "Eşya Durumu")
            is_furnished = _detect_furnished(furniture_value) if furniture_value else None

        seller_type = listing.seller_type
        if seller_type is None:
            seller_value = _value_after_label(detail_text, "Kimden")
            seller_type = _detect_seller_type(seller_value) if seller_value else _detect_seller_type(listing.title)
            authorized_office = _value_after_label(detail_text, "Yetkili Ofis")
            if seller_type is None and _normalize_text(authorized_office) == "evet":
                seller_type = "emlak"

        enriched.append(
            listing.model_copy(
                update={
                    "title": title,
                    "room_count": room_count,
                    "is_furnished": is_furnished,
                    "seller_type": seller_type,
                    "description": detail_text[:1000],
                }
            )
        )

    if len(listings) > 30:
        enriched.extend(listings[30:])

    return enriched


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
        if not title:
            title = _title_from_url(full_url)
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
                    if response.status_code == 200:
                        _set_hepsiemlak_status("ok")
                        logging.info("Hepsiemlak cevap verdi: %s", url)
                        return response.text

                    if response.status_code == 403:
                        _set_hepsiemlak_status(
                            "blocked",
                            "Hepsiemlak Google Cloud VM IP adresinden gelen istekleri 403 ile engelliyor.",
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
                    _set_hepsiemlak_status("ok")
                    return response.text
                if response.status_code == 403:
                    _set_hepsiemlak_status(
                        "blocked",
                        "Hepsiemlak Google Cloud VM IP adresinden gelen istekleri 403 ile engelliyor.",
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

                if response.status_code == 200 and body_size > 50_000:
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


async def _fetch_hepsiemlak(
    city: str,
    district: str,
    listing_type: str = "kiralik",
    property_type: str = "daire",
    min_price: float = None,
    max_price: float = None,
) -> list[ListingModel]:
    _set_hepsiemlak_status("checking")
    slug = _normalize_slug(district or city)
    property_segment = _hepsiemlak_property_segment(property_type)
    urls = [
        f"https://www.hepsiemlak.com/{slug}-{listing_type}/{property_segment}",
        f"https://www.hepsiemlak.com/{slug}-{listing_type}",
    ]

    logging.info("Hepsiemlak taraniyor: %s", ", ".join(urls))

    html = await _fetch_hepsiemlak_unlocker_api(urls)

    if html:
        logging.info("Hepsiemlak Bright Data Unlocker ile HTML alindi.")
    else:
        logging.info("Hepsiemlak Bright Data Unlocker kullanilamadi/bos dondu; native proxy deneniyor.")

    try:
        if not html:
            html = await asyncio.to_thread(_fetch_hepsiemlak_sync, urls)
    except Exception as e:
        logging.error("Hepsiemlak hatasi: %s", e)
        html = ""

    if not html:
        logging.warning("Hepsiemlak curl ile bos dondu; httpx fallback deneniyor.")
        html = await _fetch_hepsiemlak_httpx_fallback(urls)

    if not html:
        if SOURCE_STATUS["hepsiemlak"]["state"] != "blocked":
            _set_hepsiemlak_status("unavailable", "Hepsiemlak HTML alinamadi.")
        logging.error("Hepsiemlak HTML alinamadi: %s", ", ".join(urls))
        return []

    soup = BeautifulSoup(html, "html.parser")
    listings = _parse_hepsiemlak_cards(soup, source_prefix="he", base_url="https://www.hepsiemlak.com")
    logging.info("Hepsiemlak kart parse sonucu: %s", len(listings))
    if not listings:
        _set_hepsiemlak_status("parse_empty", "Hepsiemlak sayfasi acildi ama ilan karti parse edilemedi.")
    listings = await asyncio.to_thread(_enrich_hepsiemlak_details_sync, listings)

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
