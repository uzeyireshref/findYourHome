import argparse
import asyncio
import json
import sqlite3
import sys
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


async def _run(criteria: dict, limit: int | None) -> None:
    listings = await fetch_listings(criteria)
    rows = build_filter_debug_rows(listings, criteria)

    print(f"\n=== HAM TOPLAM: {len(rows)} ===")
    shown = rows if not limit else rows[:limit]
    for idx, row in enumerate(shown, 1):
        print(_format_row(idx, row))

    matched = sum(1 for row in rows if row["matched"])
    print(f"\n=== OZET: {matched} uygun / {len(rows)} toplam ===")


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
    args = parser.parse_args()

    if args.criteria_json:
        criteria = json.loads(args.criteria_json)
    else:
        criteria = _load_criteria(args.criteria_file, args.db_path)
    _print_header(criteria)
    asyncio.run(_run(criteria, args.limit))


if __name__ == "__main__":
    main()
