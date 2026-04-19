# Deployment Guide

Bu proje live ortamda su sekilde konumlandirilabilir:

1. Kod GitHub'a yuklenir.
2. Supabase uzerinde Postgres database olusturulur.
3. Google Cloud uzerinde container veya VM calistirilir.
4. Production ortam degiskenleri server tarafinda tanimlanir.
5. Bot surekli calisir ve Telegram polling ile mesajlari dinler.

## 1. GitHub

GitHub'a yuklemeden once su dosyalarin repoya girmediginden emin olun:

- `.env`
- `emlak.db`
- `*.log`
- `__pycache__/`

Hazirlik:

```bash
git status --ignored --short
git add .
git commit -m "Initial production-ready bot setup"
git branch -M main
git remote add origin https://github.com/<username>/<repo>.git
git push -u origin main
```

## 2. Supabase

Supabase'de yeni bir project olusturun ve database connection string alin.

Uygulama `postgresql://...` veya `postgres://...` formatindaki URL'leri otomatik olarak SQLAlchemy async formatina cevirir.

Production `DATABASE_URL` ornegi:

```env
DATABASE_URL=postgresql://postgres:<password>@<host>:5432/postgres
```

Ilk calismada SQLAlchemy tabloları otomatik olusturur.

## 3. Google Cloud Secenekleri

### Onerilen baslangic: Compute Engine VM

Bu bot Telegram long polling kullandigi icin en kolay production modeli bir VM uzerinde surekli calistirmaktir.

Akis:

```bash
git clone https://github.com/<username>/<repo>.git
cd <repo>
cp .env.example .env
docker compose up -d --build
```

`.env` icinde production secret'larini doldurun:

```env
TELEGRAM_BOT_TOKEN=...
GEMINI_API_KEY=...
DATABASE_URL=postgresql://...
MIN_GEMINI_SCORE=60
```

### Alternatif: Cloud Run

Cloud Run kullanilabilir, fakat long polling icin minimum instance ve surekli calisma ayarlari gerekir. Daha iyi Cloud Run mimarisi icin botu webhook moduna cevirmek gerekir.

## 4. Production Checklist

- Telegram token yenilenmeli ve sadece production `.env` tarafinda tutulmali.
- Supabase project password ve connection string GitHub'a konmamalı.
- Google Cloud VM firewall disari acik port gerektirmez; bot Telegram'a outbound istek atar.
- Loglar serverda dosya yerine Cloud Logging veya `docker logs` ile takip edilmeli.
- Scraper sitelerinin HTML yapisi degisebilecegi icin periyodik kontrol edilmeli.
