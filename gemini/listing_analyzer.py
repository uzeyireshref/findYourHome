import json
import os
import logging
from google import genai
from typing import List, Dict

async def analyze_listings_batch(listings: List[Dict], criteria: Dict) -> List[Dict]:
    """
    Birden fazla ilanı tek seferde AI süzgecinden geçirir.
    Returns: Sadece kriterlere uyan, AI tarafından onaylanmış ilan listesi.
    """
    if not listings:
        return []

    if not os.getenv("GEMINI_API_KEY"):
        logging.error("GEMINI_API_KEY bulunamadi!")
        return listings # API key yoksa filtreleme yapmadan dondur

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    
    # AI'ya kriterlerimizi ve ilan listesini bir prompt olarak hazırlıyoruz
    listings_text = ""
    for i, item in enumerate(listings):
        # AI'nin okuyacagi metni hazirla
        desc = (item.get('description') or "")[:500]
        listings_text += (
            f"\n--- ILAN #{i} ---\n"
            f"ID: {item.get('listing_id')}\n"
            f"Baslik: {item.get('title')}\n"
            f"Fiyat: {item.get('price')} TL\n"
            f"Konum: {item.get('district')}\n"
            f"Oda: {item.get('room_count')}\n"
            f"Metin: {desc}\n"
        )

    prompt = f"""
Analyze these Turkish real estate listings based on the user's criteria. 
Identify which listings are a match. Be smart: if room count isn't explicitly in the field but is mentioned in the description, count it.
Ignore listings that clearly violate price or location criteria.

USER CRITERIA:
{json.dumps(criteria, ensure_ascii=False, indent=2)}

LISTINGS TO ANALYZE:
{listings_text}

Response must be ONLY a JSON object with the list of suitable IDs. 
Example: {{"suitable_ids": ["ej_123", "he_456"]}}
"""

    try:
        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        
        res_text = response.text.strip()
        if "```json" in res_text:
            res_text = res_text.split("```json")[1].split("```")[0].strip()
        elif res_text.startswith("```"):
             res_text = res_text.strip("`").strip()
             if res_text.startswith("json"):
                 res_text = res_text[4:].strip()
        
        result = json.loads(res_text)
        suitable_ids = result.get("suitable_ids", [])
        
        # Sadece AI'nın onayladığı ilanları geri döndür
        approved = [l for l in listings if str(l.get('listing_id')) in [str(sid) for sid in suitable_ids]]
        logging.info(f"AI Analizi: {len(listings)} ilandan {len(approved)} tanesi uygun bulundu.")
        return approved
        
    except Exception as e:
        logging.error(f"AI Batch Analiz Hatasi: {e}")
        # Hata durumunda veriyi kaybetmemek icin orijinal listeyi donduruyoruz (guvenli liman)
        return listings
