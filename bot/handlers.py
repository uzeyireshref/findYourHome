import html
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import get_confirm_criteria_keyboard
from db.crud import (
    add_search_criteria,
    check_if_listing_seen,
    get_or_create_user,
    mark_listing_as_seen,
)
from db.database import AsyncSessionLocal
from filters.basic import apply_basic_filters
from gemini.criteria_parser import parse_user_request
from notifications.sender import send_new_listing_notification
from scraper.sahibinden import fetch_listings, get_source_status

MAX_INITIAL_LISTINGS = 10


def _format_price(value):
    if value is None:
        return None
    return f"{value:,.0f}".replace(",", ".") + " TL"


def _criteria_lines(criteria: dict) -> list[str]:
    lines = ["📋 <b>Belirlediğim kriterler</b>", ""]

    if criteria.get("city"):
        lines.append(f"🏙 <b>Şehir:</b> {html.escape(str(criteria['city']))}")
    if criteria.get("district"):
        lines.append(f"📍 <b>İlçe(ler):</b> {html.escape(str(criteria['district']))}")
    if criteria.get("listing_type"):
        lines.append(f"🏷 <b>İlan türü:</b> {html.escape(str(criteria['listing_type']))}")
    if criteria.get("property_type"):
        lines.append(f"🏠 <b>Konut tipi:</b> {html.escape(str(criteria['property_type']))}")
    if criteria.get("min_price"):
        lines.append(f"💰 <b>Min fiyat:</b> {_format_price(criteria['min_price'])}")
    if criteria.get("max_price"):
        lines.append(f"💰 <b>Max fiyat:</b> {_format_price(criteria['max_price'])}")
    if criteria.get("min_rooms"):
        lines.append(f"🛏 <b>Oda:</b> En az {criteria['min_rooms']}")
    if criteria.get("max_rooms"):
        lines.append(f"🛏 <b>Max oda:</b> {criteria['max_rooms']}")
    if criteria.get("max_building_age"):
        lines.append(f"🏢 <b>Max bina yaşı:</b> {criteria['max_building_age']}")
    if criteria.get("is_furnished") is not None:
        furnished = "Evet" if criteria["is_furnished"] else "Hayır"
        lines.append(f"🛋 <b>Eşyalı:</b> {furnished}")
    if criteria.get("seller_type"):
        lines.append(f"👤 <b>Satıcı:</b> {html.escape(str(criteria['seller_type']))}")
    if criteria.get("extra_notes"):
        lines.append(f"📝 <b>Özel istekler:</b> {html.escape(str(criteria['extra_notes']))}")

    lines.append("")
    lines.append("Bu kriterlerle aramayı kaydedeyim mi?")
    return lines


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username

    async with AsyncSessionLocal() as session:
        await get_or_create_user(session, telegram_id=user_id, username=username)

    await update.message.reply_text(
        "Merhaba! Ben Evaravebul.\n\n"
        "Aradığın evi normal cümleyle yazman yeterli. Örnek:\n"
        "/ara İstanbul Kadıköy'de 45 bin TL altı, eşyalı, en az 2 odalı ev arıyorum"
    )


async def ara_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text and update.message.text.startswith("/ara"):
        user_text = " ".join(context.args)
    else:
        user_text = update.message.text

    if not user_text or not user_text.strip():
        await update.message.reply_text(
            "Aradığın evi birkaç kelimeyle yazabilir misin?\n"
            "Örnek: Kadıköy 3+1 45 bin TL altı eşyalı"
        )
        return

    loading_msg = await update.message.reply_text(
        "🤖 Kriterlerini analiz ediyorum, birkaç saniye sürebilir..."
    )

    try:
        parsed_criteria = await parse_user_request(user_text)
        context.user_data["temp_criteria"] = parsed_criteria

        await loading_msg.delete()
        await update.message.reply_text(
            "\n".join(_criteria_lines(parsed_criteria)),
            parse_mode="HTML",
            reply_markup=get_confirm_criteria_keyboard(),
        )

    except Exception as e:
        logging.exception("Kriterler analiz edilirken hata oluştu")
        await loading_msg.edit_text(f"❌ Kriterler analiz edilirken hata oluştu:\n{str(e)}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_criteria":
        criteria = context.user_data.get("temp_criteria")
        if not criteria:
            await query.edit_message_text(
                "Bu butonun kriter bilgisi artık bellekte yok. Aynı aramayı tekrar yazarsan hemen yenisini hazırlayayım."
            )
            return

        telegram_id = update.effective_user.id
        await query.edit_message_text("⏳ Kriterlerini kaydediyorum...")

        try:
            async with AsyncSessionLocal() as session:
                user = await get_or_create_user(session, telegram_id)
                user_id = user.id
                await add_search_criteria(
                    session,
                    user_id=user_id,
                    criteria_data=criteria,
                )
        except Exception as e:
            logging.exception("Kriter kaydedilemedi")
            await query.edit_message_text(f"❌ Kriterler kaydedilemedi:\n{str(e)}")
            return

        context.user_data["temp_criteria"] = None
        await query.edit_message_text(
            "✅ Kriterlerin kaydedildi.\n"
            "🔎 Şimdi Emlakjet ve Hepsiemlak ilanlarını tarıyorum..."
        )

        try:
            listings = await fetch_listings(criteria)
        except Exception as e:
            logging.exception("İlk tarama sırasında hata oluştu")
            await context.bot.send_message(
                chat_id=telegram_id,
                text=(
                    "⚠️ Kriterler kaydedildi ama ilk tarama sırasında hata oluştu:\n"
                    f"{str(e)}\n\n"
                    "Arka plan taraması yine de devam edecek."
                ),
            )
            return

        ej_count = len([l for l in listings if l.listing_id.startswith("ej_")])
        he_count = len([l for l in listings if l.listing_id.startswith("he_")])
        hepsiemlak_status = get_source_status().get("hepsiemlak", {})

        filtered_listings = apply_basic_filters(listings, criteria)
        preview_listings = filtered_listings[:MAX_INITIAL_LISTINGS]

        ej_filtered = len([l for l in filtered_listings if l.listing_id.startswith("ej_")])
        he_filtered = len([l for l in filtered_listings if l.listing_id.startswith("he_")])

        seen_map = {}
        async with AsyncSessionLocal() as session:
            for listing in preview_listings:
                seen_map[listing.listing_id] = await check_if_listing_seen(
                    session,
                    user_id=user_id,
                    listing_id=listing.listing_id,
                )

        seen_preview_count = sum(1 for is_seen in seen_map.values() if is_seen)

        hepsiemlak_line = (
            f"• Hepsiemlak: <b>{he_count}</b> ilan çekildi → <b>{he_filtered}</b> uygun"
        )
        if he_count == 0 and hepsiemlak_status.get("state") == "blocked":
            hepsiemlak_line = (
                "• Hepsiemlak: <b>erişim engellendi</b> "
                "(Google Cloud IP 403 alıyor)"
            )

        stats_msg = (
            "📊 <b>Tarama özeti</b>\n\n"
            f"• Emlakjet: <b>{ej_count}</b> ilan çekildi → <b>{ej_filtered}</b> uygun\n"
            f"{hepsiemlak_line}\n"
            f"• Toplam: <b>{len(listings)}</b> ilan çekildi → <b>{len(filtered_listings)}</b> uygun\n"
            f"• Listelenecek: <b>{len(preview_listings)}</b> / {MAX_INITIAL_LISTINGS}\n"
            f"• Daha önce gösterilmiş: <b>{seen_preview_count}</b>"
        )
        await context.bot.send_message(
            chat_id=telegram_id,
            text=stats_msg,
            parse_mode="HTML",
        )

        if not filtered_listings:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=(
                    "🧐 Bu kriterlere tam uyan ilan bulamadım.\n\n"
                    "Eşyalı, sahibinden veya ilçe gibi net şartlarda bilgi belirsizse ilanı güvenli tarafta kalıp eliyorum. "
                    "İstersen kriteri biraz genişletip yeniden arayabilirsin."
                ),
            )
            return

        sent_count = 0
        new_count = 0

        async with AsyncSessionLocal() as session:
            for listing in preview_listings:
                is_seen = seen_map.get(listing.listing_id, False)
                summary = (
                    "Bu ilan daha önce gösterilmişti; yeni aramanla tekrar eşleştiği için ön izleme olarak yeniden gösteriyorum."
                    if is_seen
                    else "Bu ilan yeni arama kriterlerinle eşleşiyor."
                )

                success = await send_new_listing_notification(
                    context.bot,
                    chat_id=telegram_id,
                    listing=listing,
                    analysis_summary=summary,
                )
                if not success:
                    continue

                sent_count += 1
                if not is_seen:
                    await mark_listing_as_seen(
                        session,
                        user_id=user_id,
                        listing_id=listing.listing_id,
                    )
                    new_count += 1

        if sent_count == 0:
            await context.bot.send_message(
                chat_id=telegram_id,
                text="⚠️ Uygun ilan vardı ama Telegram'a gönderirken sorun yaşadım. Logları kontrol edeceğim.",
            )
            return

        await context.bot.send_message(
            chat_id=telegram_id,
            text=(
                f"✅ {sent_count} ilan listeledim.\n"
                f"🆕 Yeni görülen: {new_count}\n"
                f"👀 Önceden gösterilmiş: {sent_count - new_count}\n\n"
                "Arka planda yalnızca yeni düşen ilanları bildireceğim."
            ),
        )

    elif query.data == "cancel_criteria":
        context.user_data["temp_criteria"] = None
        await query.edit_message_text("❌ Kriter kaydı iptal edildi.")
