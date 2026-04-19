import html

from telegram.ext import ExtBot


def _format_price(price: float) -> str:
    if not price:
        return "Bilinmiyor"
    return f"{price:,.0f}".replace(",", ".") + " TL"


def _listing_source(listing_id: str) -> str:
    if listing_id.startswith("ej_"):
        return "Emlakjet"
    if listing_id.startswith("he_"):
        return "Hepsiemlak"
    return "Kaynak"


async def send_new_listing_notification(
    bot: ExtBot,
    chat_id: int,
    listing,
    analysis_summary: str,
):
    title = html.escape(listing.title or "Kiralık daire")
    district = html.escape(listing.district or "Bilinmiyor")
    room = html.escape(listing.room_count or "?")
    source = html.escape(_listing_source(listing.listing_id))
    url = html.escape(listing.url)
    summary = html.escape(analysis_summary or "Detayları linkten kontrol edebilirsiniz.")

    furnished = "Evet" if listing.is_furnished is True else "Hayır" if listing.is_furnished is False else "Belirsiz"
    seller = html.escape(listing.seller_type.capitalize() if listing.seller_type else "Belirsiz")

    message = (
        "🏠 <b>Uygun İlan</b>\n\n"
        f"<b>{title}</b>\n"
        f"📍 <b>Konum:</b> {district}\n"
        f"💰 <b>Fiyat:</b> {_format_price(listing.price)}\n"
        f"🛏 <b>Oda:</b> {room}\n"
        f"🛋 <b>Eşyalı:</b> {furnished}\n"
        f"👤 <b>Satıcı:</b> {seller}\n"
        f"🌐 <b>Kaynak:</b> {source}\n\n"
        f"ℹ️ {summary}\n\n"
        f"🔗 <a href=\"{url}\">İlanı görüntüle</a>"
    )

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=False,
        )
        return True
    except Exception as e:
        print(f"Telegram mesajı gönderilemedi: {e}")
        return False
