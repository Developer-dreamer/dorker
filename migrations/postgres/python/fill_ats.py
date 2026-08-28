import asyncio
import csv
from pathlib import Path
import asyncpg

ROOT = Path(__file__).resolve().parent.parent.parent.parent
BASE_FOLDER = ROOT / "ats-companies"

PG_DSN = "postgresql://postgres:password@localhost:5432/dorker_db"

async def insert_ats_file(csv_path: Path, tier: int) -> None:
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

    conn = await asyncpg.connect(PG_DSN)
    try:
        await conn.executemany("""
            INSERT INTO companies (ats, slug, name, url, tier)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT DO NOTHING
        """, records)

        stored_count = await conn.fetchval(
            "SELECT count(*) FROM companies WHERE ats = $1", ats_name
        )
        print(f"[+] {csv_path.name:<25} | Parsed: {len(records):>5} | In DB for '{ats_name}': {stored_count:>5}")
    finally:
        await conn.close()

async def main() -> None:
    for tier_num in (1, 2, 3):
        tier_dir = BASE_FOLDER / str(tier_num)
        if not tier_dir.is_dir():
            continue

        csv_files = sorted([f for f in tier_dir.iterdir() if f.is_file() and f.suffix == ".csv"])
        print(f"\n--- Processing Tier {tier_num} ({len(csv_files)} files) ---")

        for csv_file in csv_files:
            try:
                await insert_ats_file(csv_file, tier_num)
            except Exception as e:
                print(f"[!] ERROR in {csv_file.name}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
    