import re
import sys

def main():
    try:
        with open("scraper/sahibinden.py", "r", encoding="utf-8") as f:
            content = f.read()

        # Remove _enrich_hepsiemlak_details_sync_legacy_unused
        start_legacy = content.find("def _enrich_hepsiemlak_details_sync_legacy_unused")
        end_legacy = content.find("def _enrich_emlakjet_details_sync", start_legacy)
        
        if start_legacy != -1 and end_legacy != -1:
            content = content[:start_legacy] + content[end_legacy:]

        # Update _enrich_emlakjet_details_sync to async
        old_ej = "def _enrich_emlakjet_details_sync(listings: list[ListingModel], criteria: dict | None = None) -> list[ListingModel]:"
        start_ej = content.find(old_ej)
        end_ej = content.find("def _enrich_hepsiemlak_details_sync", start_ej)
        
        new_ej_block = """async def _enrich_emlakjet_single(client: httpx.AsyncClient, listing: ListingModel, criteria: dict | None) -> ListingModel:
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

"""
        
        content = content[:start_ej] + new_ej_block + content[end_ej:]

        # Now Hepsiemlak
        old_he = "def _enrich_hepsiemlak_details_sync(listings: list[ListingModel], criteria: dict | None = None) -> list[ListingModel]:"
        start_he = content.find(old_he)
        end_he = content.find("async def _fetch_emlakjet", start_he)

        new_he_block = """def _enrich_hepsiemlak_single_sync(listing: ListingModel, criteria: dict | None, session, proxies, unlocker_client) -> ListingModel:
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

"""
        content = content[:start_he] + new_he_block + content[end_he:]

        with open("scraper/sahibinden.py", "w", encoding="utf-8") as f:
            f.write(content)
        print("Success deep")
    except Exception as e:
        print("Error:", str(e))

if __name__ == "__main__":
    main()
