import os
import logging
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db.database import init_db
from bot.handlers import start_handler, ara_handler, button_handler
from scheduler.jobs import run_scraper_job

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Loglama yapılandırması (Adım 9)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

async def post_init(application):
    """
    Bot ayağa kalkmadan önce veritabanı tablolarını senkronize et.
    """
    logging.info("Veritabanı tabloları oluşturuluyor/kontrol ediliyor...")
    await init_db()

    # Zamanlayıcı eklendi (Adım 8)
    logging.info("Scheduler başlatılıyor...")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_scraper_job,
        'cron',
        hour='8,11,14,17,20',
        args=[application]
    )
    scheduler.start()

def main():
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN .env dosyasında girilmemiş!")
        return

    # Uygulamayı başlat
    application = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Bot komutları eklendi
    application.add_handler(CommandHandler('start', start_handler))
    application.add_handler(CommandHandler('ara', ara_handler))
    # Normal metin atıldığında da ara_handler çalışsın
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ara_handler))
    application.add_handler(CallbackQueryHandler(button_handler))

    logging.info("Bot başlatılıyor. Dinleniyor...")
    application.run_polling()

if __name__ == '__main__':
    main()
