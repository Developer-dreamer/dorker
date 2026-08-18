from pathlib import Path

import aiosqlite


async def run_migrations(db_path: str | Path, migrations_dir: str | Path) -> None:
    migrations_path = Path(migrations_dir)

    if not migrations_path.exists() or not migrations_path.is_dir():
        raise FileNotFoundError(f"Migrations directory not found: {migrations_path}")

    # Sorting is mandatory to maintain sequential execution (e.g., 001_init.sql, 002_update.sql)
    migration_files = sorted(migrations_path.glob("*.sql"))

    async with aiosqlite.connect(db_path) as db:
        # 1. Initialize migration tracking table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()

        # 2. Retrieve history of applied migrations
        async with db.execute("SELECT filename FROM schema_migrations") as cursor:
            applied_migrations = {row[0] async for row in cursor}

        # 3. Execute pending migrations
        for file_path in migration_files:
            filename = file_path.name

            if filename not in applied_migrations:
                sql_content = file_path.read_text(encoding="utf-8")

                try:
                    # executescript is required for multi-statement .sql files
                    await db.executescript(sql_content)

                    # Record successful execution
                    await db.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (?)",
                        (filename,)
                    )
                    await db.commit()
                except Exception as e:
                    # Rollback implicitly handled if not committed, 
                    # but explicit log/raise is required to stop the pipeline
                    raise RuntimeError(f"Migration failed on {filename}: {e}")

def save_job() -> None:
    pass

def job_exists() -> bool:
    return False

def save_match() -> None:
    pass

