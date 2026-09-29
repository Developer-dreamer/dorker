import asyncio
import csv
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parent.parent.parent.parent
BASE_FOLDER = (
    ROOT / "artifacts" / "data" / "ats-companies"
    if (ROOT / "artifacts" / "data" / "ats-companies").exists()
    else ROOT / "artifacts" / "database" / "ats-companies"
)

PG_DSN = "postgresql://postgres:password@localhost:5432/dorker_db"


async def insert_ats_file(conn: asyncpg.Connection, csv_path: Path, tier: int) -> tuple[int, int]:
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
        return 0, 0

    query = """
        INSERT INTO companies (ats, slug, name, url, tier)
        SELECT * FROM unnest(
            $1::text[],
            $2::text[],
            $3::text[],
            $4::text[],
            $5::int[]
        )
        ON CONFLICT (ats, name, slug, url) DO NOTHING
        RETURNING id;
    """

    inserted = await conn.fetch(
        query,
        [r[0] for r in records],
        [r[1] for r in records],
        [r[2] for r in records],
        [r[3] for r in records],
        [r[4] for r in records],
    )

    new_count = len(inserted)
    dupe_count = len(records) - new_count
    stored_count = await conn.fetchval("SELECT count(*) FROM companies WHERE ats = $1", ats_name)

    print(
        f"[+] {csv_path.name:<25} | Parsed: {len(records):>5} | "
        f"New: {new_count:>5} | Existing: {dupe_count:>5} | Total in DB: {stored_count:>5}"
    )
    return len(records), new_count


async def main() -> None:
    total_parsed = 0
    total_new = 0

    conn = await asyncpg.connect(PG_DSN)
    try:
        for tier_num in (1, 2, 3):
            tier_dir = BASE_FOLDER / str(tier_num)
            if not tier_dir.is_dir():
                continue

            csv_files = sorted(
                [f for f in tier_dir.iterdir() if f.is_file() and f.suffix == ".csv"]
            )
            print(f"\n--- Processing Tier {tier_num} ({len(csv_files)} files) ---")

            for csv_file in csv_files:
                try:
                    parsed, new = await insert_ats_file(conn, csv_file, tier_num)
                    total_parsed += parsed
                    total_new += new
                except Exception as e:
                    print(f"[!] ERROR in {csv_file.name}: {e}")

        print(f"\n{'=' * 65}")
        print(
            f"SUMMARY: Parsed {total_parsed:,} total rows | "
            f"New leads added: {total_new:,} | "
            f"Already existed: {total_parsed - total_new:,}"
        )
        print(f"{'=' * 65}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
