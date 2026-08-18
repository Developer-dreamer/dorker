import csv
import sqlite3
from pathlib import Path

# Force absolute paths relative to this script's location
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_FOLDER = Path("/Users/serafym/Developer/dorker.space/intelligence_core/ats-companies")
DB_PATH = Path("/Users/serafym/Developer/dorker.space/intelligence_core/app.db")


def inspect_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='ats';")
    schema = cursor.fetchone()
    print("CURRENT ATS SCHEMA IN app.db:")
    print(schema[0] if schema else "[!] TABLE 'ats' DOES NOT EXIST")
    print("=" * 80)


def insert_ats_file(csv_path: Path, db_path: Path, tier: int) -> None:
    ats_name = csv_path.stem

    with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        records = [
            (ats_name, row["slug"].strip(), row["name"].strip(), row["url"].strip(), tier)
            for row in reader
            if row.get("slug") and row["slug"].strip()
        ]

    if not records:
        print(f"[-] {csv_path.name}: 0 valid rows parsed.")
        return

    # Use strict absolute path
    with sqlite3.connect(str(db_path.resolve()), autocommit=False) as conn:
        cursor = conn.cursor()

        # Direct Upsert
        cursor.executemany("""
            INSERT INTO ats (ats_name, company_slug, company_name, url, tier)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ats_name, company_slug) DO UPDATE SET
                company_name = excluded.company_name,
                url = excluded.url,
                tier = excluded.tier;
        """, records)

        conn.commit()

        # Immediate verification query on the same connection
        cursor.execute("SELECT count(*) FROM ats WHERE ats_name = ?", (ats_name,))
        stored_count = cursor.fetchone()[0]
        print(f"[+] {csv_path.name:<25} | Parsed: {len(records):>5} | In DB for '{ats_name}': {stored_count:>5}")


def main() -> None:
    print(f"Target Database Absolute Path: {DB_PATH.resolve()}")

    if not DB_PATH.exists():
        print(f"[!] Target database file does not exist at: {DB_PATH.resolve()}")
        return

    with sqlite3.connect(str(DB_PATH.resolve())) as conn:
        inspect_schema(conn)

    for tier_num in (1, 2, 3):
        tier_dir = BASE_FOLDER / str(tier_num)
        if not tier_dir.is_dir():
            continue

        csv_files = sorted([f for f in tier_dir.iterdir() if f.is_file() and f.suffix == ".csv"])
        print(f"\n--- Processing Tier {tier_num} ({len(csv_files)} files) ---")

        for csv_file in csv_files:
            try:
                insert_ats_file(csv_file, DB_PATH, tier_num)
            except Exception as e:
                print(f"[!] ERROR in {csv_file.name}: {e}")

    # Final tally check
    print("\n" + "=" * 80)
    print("FINAL DATABASE TOTALS:")
    with sqlite3.connect(str(DB_PATH.resolve())) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT ats_name, count(*) FROM ats GROUP BY ats_name ORDER BY count(*) DESC;")
        rows = cursor.fetchall()
        for ats_name, count in rows:
            print(f"  - {ats_name:<20}: {count:>6} rows")
        cursor.execute("SELECT count(*) FROM ats;")
        print(f"TOTAL ROWS: {cursor.fetchone()[0]}")
    print("=" * 80)


if __name__ == "__main__":
    main()
