import argparse
import asyncio
import html
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from filters.basic import build_filter_debug_rows
from scraper.sahibinden import fetch_listings


def _load_criteria_from_db(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT city, district, listing_type, property_type,
               min_price, max_price, min_rooms, max_rooms,
               max_building_age, is_furnished, seller_type, extra_notes
        FROM search_criteria
        WHERE is_active = 1
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()

    if not row:
        raise RuntimeError(f"Aktif kriter bulunamadi: {db_path}")

    criteria = dict(row)
    if criteria.get("is_furnished") in (0, 1):
        criteria["is_furnished"] = bool(criteria["is_furnished"])
    return criteria


def _load_criteria(criteria_file: str | None, db_path: str) -> dict:
    if criteria_file:
        return json.loads(Path(criteria_file).read_text(encoding="utf-8"))
    return _load_criteria_from_db(db_path)


def _print_header(criteria: dict) -> None:
    print("=== KRITERLER ===")
    print(json.dumps(criteria, ensure_ascii=False, indent=2))


def _format_row(index: int, row: dict) -> str:
    source = "HE" if str(row["listing_id"]).startswith("he_") else "EJ"
    status = "UYGUN" if row["matched"] else "ELENDI"
    reasons = " | ".join(row["reasons"])
    return (
        f"{index:03d} [{source}] {status} | {reasons} | "
        f"fiyat={row['price']} | ilce={row['district']} | oda={row['room_count']} | "
        f"yas={row['building_age']} | esyali={row['is_furnished']} | "
        f"satici={row['seller_type']} | id={row['listing_id']} | url={row['url']}"
    )


def _write_html_report(criteria: dict, rows: list[dict], html_out: str) -> None:
    matched = sum(1 for row in rows if row["matched"])
    unmatched = len(rows) - matched
    generated_at = datetime.now(timezone.utc).isoformat()

    table_rows: list[str] = []
    for row in rows:
        source = "HE" if str(row["listing_id"]).startswith("he_") else "EJ"
        status = "UYGUN" if row["matched"] else "ELENDI"
        css_class = "ok" if row["matched"] else "bad"
        reasons = " | ".join(row["reasons"])
        table_rows.append(
            "<tr class='{cls}'>"
            "<td>{status}</td><td>{source}</td><td>{listing_id}</td><td>{price}</td>"
            "<td>{district}</td><td>{room_count}</td><td>{building_age}</td>"
            "<td>{is_furnished}</td><td>{seller_type}</td><td>{reasons}</td><td>{url}</td>"
            "</tr>".format(
                cls=css_class,
                status=html.escape(status),
                source=html.escape(source),
                listing_id=html.escape(str(row["listing_id"])),
                price=html.escape(str(row["price"])),
                district=html.escape(str(row["district"])),
                room_count=html.escape(str(row["room_count"])),
                building_age=html.escape(str(row["building_age"])),
                is_furnished=html.escape(str(row["is_furnished"])),
                seller_type=html.escape(str(row["seller_type"])),
                reasons=html.escape(reasons),
                url=html.escape(str(row["url"])),
            )
        )

    doc = f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Filter Debug Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 16px; background: #f5f7fb; color: #111; }}
    .meta {{ margin-bottom: 14px; padding: 12px; background: #fff; border: 1px solid #ddd; border-radius: 8px; }}
    .counts b {{ margin-right: 12px; }}
    pre {{ background: #111; color: #eee; padding: 10px; border-radius: 8px; overflow: auto; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #ddd; }}
    th, td {{ border: 1px solid #ddd; padding: 6px; font-size: 12px; vertical-align: top; }}
    th {{ position: sticky; top: 0; background: #f0f0f0; z-index: 1; }}
    tr.ok {{ background: #ecfff3; }}
    tr.bad {{ background: #fff1f1; }}
  </style>
</head>
<body>
  <h2>Ilan Filtre Debug Raporu</h2>
  <div class="meta">
    <div class="counts">
      <b>Toplam: {len(rows)}</b>
      <b>Uygun: {matched}</b>
      <b>Elenen: {unmatched}</b>
    </div>
    <div>Uretim zamani (UTC): {html.escape(generated_at)}</div>
    <h3>Kriterler</h3>
    <pre>{html.escape(json.dumps(criteria, ensure_ascii=False, indent=2))}</pre>
  </div>
  <table>
    <thead>
      <tr>
        <th>Sonuc</th>
        <th>Kaynak</th>
        <th>ID</th>
        <th>Fiyat</th>
        <th>Ilce</th>
        <th>Oda</th>
        <th>Bina Yasi</th>
        <th>Esyali</th>
        <th>Satici</th>
        <th>Neden</th>
        <th>URL</th>
      </tr>
    </thead>
    <tbody>
      {''.join(table_rows)}
    </tbody>
  </table>
</body>
</html>
"""
    Path(html_out).write_text(doc, encoding="utf-8")


async def _run(criteria: dict, limit: int | None, html_out: str | None) -> None:
    listings = await fetch_listings(criteria, debug_mode=True)
    rows = build_filter_debug_rows(listings, criteria)

    print(f"\n=== HAM TOPLAM: {len(rows)} ===")
    shown = rows if not limit else rows[:limit]
    for idx, row in enumerate(shown, 1):
        print(_format_row(idx, row))

    matched = sum(1 for row in rows if row["matched"])
    print(f"\n=== OZET: {matched} uygun / {len(rows)} toplam ===")
    if html_out:
        html_rows = rows if not limit else rows[:limit]
        _write_html_report(criteria, html_rows, html_out)
        print(f"=== HTML RAPOR: {html_out} ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ilan eleme debug raporu")
    parser.add_argument(
        "--criteria-file",
        help="JSON kriter dosyasi. Verilmezse db'den aktif kriter okunur.",
    )
    parser.add_argument(
        "--criteria-json",
        help="JSON kriterini dogrudan arguman olarak ver. Ornek: '{\"city\":\"istanbul\"}'",
    )
    parser.add_argument(
        "--db-path",
        default="emlak.db",
        help="SQLite db yolu (varsayilan: emlak.db)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Sadece ilk N kaydi goster",
    )
    parser.add_argument(
        "--html-out",
        help="HTML rapor dosya yolu. Ornek: /app/filter_debug.html",
    )
    args = parser.parse_args()

    if args.criteria_json:
        criteria = json.loads(args.criteria_json)
    else:
        criteria = _load_criteria(args.criteria_file, args.db_path)
    _print_header(criteria)
    asyncio.run(_run(criteria, args.limit, args.html_out))


if __name__ == "__main__":
    main()
