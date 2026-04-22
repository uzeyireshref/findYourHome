import asyncio
import os
import sys
from pathlib import Path

# Proje kok dizinini ekle
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from db.database import SessionLocal
from db.models import Listing

async def clear_queue():
    """Tum mevcut ilanlari 'gonderildi' olarak isaretler, boylece bildirim yagmuru durur."""
    print("Veritabani temizligi baslatiliyor...")
    async with SessionLocal() as session:
        # Bu kisim senin db/models.py yapina gore degisebilir. 
        # Genellikle ilanlar kaydedildiginde 'gonderildi' bayragi olur.
        # Eger oyle bir tablo yoksa, biz sadece mevcutlari 'eskidi' sayacagiz.
        print("Mevcut ilanlarin kuyrugu temizleniyor. Lutfen bekleyin...")
        # (Buraya senin veritabani yapina uygun bir 'mark as seen' sorgusu gelecek)
    print("Temizlik tamamlandi. Artik botu baslatabilirsiniz.")

if __name__ == "__main__":
    # asyncio.run(clear_queue())
    print("Direkt veritabani uzerinden (Supabase) 'sent_listings' tablosunu bosaltmak veya guncellemek daha mantikli olabilir.")
