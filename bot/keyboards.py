from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def get_confirm_criteria_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Tamam, kaydet", callback_data="confirm_criteria")],
        [InlineKeyboardButton("❌ İptal", callback_data="cancel_criteria")],
    ]
    return InlineKeyboardMarkup(keyboard)
