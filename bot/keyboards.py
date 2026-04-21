from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_confirm_criteria_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("✅ Onayla ve Aramayı Başlat", callback_data="confirm_criteria"),
            InlineKeyboardButton("❌ İptal Et", callback_data="cancel_criteria")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
