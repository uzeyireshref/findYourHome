# Evaravebul - Akilli Emlak Bildirim Botu

Telegram uzerinden calisan, kullanicinin dogal dilde yazdigi emlak kriterlerini anlayip Emlakjet ve Hepsiemlak ilanlarini takip eden bir bildirim botudur.

## Ne Yapar?

1. Kullanici Telegram'da aradigi evi yazar.
   Ornek: `istanbul kiralik daire aylik 20-40 bin tl arasi, esyali, en az 1+1, karakoy, fatih, kadikoy ilcelerinde sahibinden olan ilanlar`
2. Gemini bu metni yapilandirilmis kriterlere cevirir.
3. Bot kriterleri kullaniciya onaylatir ve SQLite veritabanina kaydeder.
4. Kayit sonrasi hemen Emlakjet ve Hepsiemlak taranir.
5. Ilanlar ilan turu, konut tipi, fiyat, ilce, oda sayisi, esyali durumu ve satici tipine gore filtrelenir.
6. Daha once gonderilmemis uygun ilanlar Telegram mesaji olarak kullaniciya iletilir.
7. Scheduler gun icinde belirli saatlerde ayni kriterleri tekrar tarar.

## Ana Dosyalar

- `main.py`: Botu baslatir, veritabanini hazirlar ve scheduler job'larini kurar.
- `bot/handlers.py`: `/start`, `/ara`, normal mesaj ve inline buton callback akisini yonetir.
- `gemini/criteria_parser.py`: Kullanici metnini Gemini ile JSON kriterlerine cevirir.
- `scraper/sahibinden.py`: Ismine ragmen Emlakjet ve Hepsiemlak scraper mantigini icerir.
- `filters/basic.py`: Temel ilan filtrelerini uygular.
- `notifications/sender.py`: Telegram ilan mesajlarini gonderir.
- `db/models.py`: SQLAlchemy tablolarini tanimlar.
- `scheduler/jobs.py`: Arka plan ilan kontrolunu calistirir.

## Veritabani

SQLite kullanilir. Varsayilan dosya: `emlak.db`

Tablolar:

- `users`
- `search_criteria`
- `seen_listings`
- `notifications_log`

`search_criteria` alanlari: `city`, `district`, `listing_type`, `property_type`, `min_price`, `max_price`, `min_rooms`, `max_rooms`, `max_building_age`, `is_furnished`, `seller_type`, `extra_notes`, `is_active`.

## Ortam Degiskenleri

`.env` icinde beklenen degerler:

```env
TELEGRAM_BOT_TOKEN=
GEMINI_API_KEY=
DATABASE_URL=sqlite+aiosqlite:///./emlak.db
MIN_GEMINI_SCORE=60
HEPSIEMLAK_PROXY=
BRIGHTDATA_API_KEY=
BRIGHTDATA_UNLOCKER_ZONE=web_unlocker1
```

Hepsiemlak notu:
- Bazi cloud IP bloklarinda Hepsiemlak 403 donebilir.
- Bu durumda `HEPSIEMLAK_PROXY` veya `BRIGHTDATA_API_KEY` tanimlanmazsa Hepsiemlak'tan ilan gelmeyebilir.

## Kurulum

```bash
pip install -r requirements.txt
python main.py
```

## Notlar

- Proje su anda sahibinden.com'u degil Emlakjet ve Hepsiemlak'i tarar.
- Ilk kayit sonrasi hizli sonuc icin ilanlar Gemini analizinden gecirilmeden gonderilir.
- Arka plan job'inda `extra_notes` icin Gemini analizi vardir, fakat scraper detay aciklamasini her zaman cekmedigi icin bu analiz sinirlidir.
