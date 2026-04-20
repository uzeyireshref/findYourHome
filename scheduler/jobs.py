import os
import logging
from telegram.ext import ExtBot

from db.database import AsyncSessionLocal
from db.crud import get_active_criteria, check_if_listing_seen, mark_listing_as_seen, log_notification
from scraper.sahibinden import fetch_listings
from filters.basic import apply_basic_filters
from gemini.analyzer import analyze_listing_with_gemini
from notifications.sender import send_new_listing_notification

from sqlalchemy.future import select
from db.models import User


def _min_gemini_score() -> int:
    try:
        return int(os.getenv("MIN_GEMINI_SCORE", "60"))
    except ValueError:
        return 60

async def run_scraper_job(context):
    bot: ExtBot = context.bot
    logging.info("Scraper job tetiklendi. Aktif kriterler kontrol ediliyor...")
    
    async with AsyncSessionLocal() as session:
        active_criterias = await get_active_criteria(session)
        
        for criteria_db in active_criterias:
            user_id = criteria_db.user_id
            
            # Kullanıcının Telegram IDsini bul
            user_res = await session.execute(select(User).where(User.id == user_id))
            user = user_res.scalar_one_or_none()
            if not user:
                continue
                
            telegram_id = user.telegram_id
            
            criteria_dict = {
                "city": criteria_db.city,
                "district": criteria_db.district,
                "min_price": criteria_db.min_price,
                "max_price": criteria_db.max_price,
                "min_rooms": criteria_db.min_rooms,
                "max_rooms": criteria_db.max_rooms,
                "max_building_age": criteria_db.max_building_age,
                "listing_type": criteria_db.listing_type,
                "property_type": criteria_db.property_type,
                "is_furnished": criteria_db.is_furnished,
                "seller_type": criteria_db.seller_type,
                "extra_notes": criteria_db.extra_notes
            }
            
            # 1. Sahibinden'den ham listeyi çek
            listings = await fetch_listings(criteria_dict)
            
            # 2. Fiyat, oda, ilçe gibi temel mekanik filtreler
            filtered_listings = apply_basic_filters(listings, criteria_dict)
            
            for listing in filtered_listings:
                # Daha önce gönderilmiş mi kontrol et
                is_seen = await check_if_listing_seen(session, user_id=user_id, listing_id=listing.listing_id)
                if is_seen:
                    continue
                
                # 3. Gemini ile semantik uygunluk değerlendirmesi
                analysis = await analyze_listing_with_gemini(
                    listing_title=listing.title,
                    listing_details=listing.description or "Araştırılıyor (Açıklama detayı scraper'dan alınamadı)",
                    extra_notes=criteria_db.extra_notes
                )
                
                uygun = analysis.get("uygun", False)
                skor = analysis.get("skor", 0)
                ozet = analysis.get("ozet", "(Bağlantıya tıklayarak detayları kontrol ediniz)")
                
                # 4. Bildirim koşulları sağlanıyorsa Telegram'a gönder
                if uygun and skor >= _min_gemini_score():
                    success = await send_new_listing_notification(bot, chat_id=telegram_id, listing=listing, analysis_summary=ozet)
                    if success:
                        await log_notification(session, user_id=user_id, listing_id=listing.listing_id, summary=ozet)
                        await mark_listing_as_seen(session, user_id=user_id, listing_id=listing.listing_id)

    logging.info("Scraper job tamamlandı.")
