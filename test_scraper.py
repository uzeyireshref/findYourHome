import sys
sys.stdout.reconfigure(encoding='utf-8')
import asyncio
import logging
logging.basicConfig(level=logging.INFO)

from scraper.sahibinden import fetch_listings

async def main():
    criteria = {
        'city': 'istanbul',
        'district': 'kadikoy',
    }
    listings = await fetch_listings(criteria)
    
    # Kaynaklara gore ayir
    ej = [l for l in listings if l.listing_id.startswith('ej_')]
    he = [l for l in listings if l.listing_id.startswith('he_')]
    
    print(f"\n=== TOPLAM {len(listings)} ILAN ===")
    print(f"  Emlakjet: {len(ej)}")
    print(f"  Hepsiemlak: {len(he)}")
    
    print(f"\n--- Emlakjet Ornekleri ---")
    for l in ej[:3]:
        print(f"  {l.title[:50]} | {l.price:,.0f} TL | {l.room_count} | {l.district[:30]}")
        print(f"  {l.url}")
    
    print(f"\n--- Hepsiemlak Ornekleri ---")
    for l in he[:3]:
        print(f"  {l.title[:50]} | {l.price:,.0f} TL | {l.room_count} | {l.district[:30]}")
        print(f"  {l.url}")

asyncio.run(main())
