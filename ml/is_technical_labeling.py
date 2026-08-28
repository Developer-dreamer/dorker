import asyncio
from typing import List, Tuple

import aiosqlite
import joblib
import torch
import uuid6
from sentence_transformers import SentenceTransformer

CHUNK_SIZE = 4096
ENCODE_BATCH_SIZE = 256


def get_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available() and torch.backends.mps.is_built():
        return "mps"
    return "cpu"


async def save_matches(conn: aiosqlite.Connection, matches: List[Tuple[str, bool]]) -> None:
    query = """
        INSERT INTO matches (id, job_id, is_technical) 
        VALUES (?, ?, ?);
    """
    records = [(str(uuid6.uuid7()), job_id, int(is_technical)) for job_id, is_technical in matches]
    await conn.executemany(query, records)


async def run() -> None:
    device = get_device()

    payload = joblib.load("tech_classifier.joblib")
    classifier = payload["classifier"]
    threshold = float(payload["optimal_threshold"])
    embedder = SentenceTransformer(
        payload["embedder_name"],
        device=device,
        local_files_only=True,
    )

    async with aiosqlite.connect("app.db") as db:
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute("PRAGMA synchronous = NORMAL;")

        # 1. B-tree set difference: get all unprocessed IDs in one pass
        # This executes in milliseconds and prevents all future table scans.
        cursor = await db.execute("""
            SELECT id FROM jobs
            EXCEPT
            SELECT job_id FROM matches;
        """)

        rows = await cursor.fetchall()
        unprocessed_ids = [row[0] for row in rows]

        if not unprocessed_ids:
            print("No unprocessed jobs remaining.")
            return

        print(f"Discovered {len(unprocessed_ids):,} unclassified jobs.")

        total_processed = 0

        # 2. Process exact chunks using IN clauses
        for i in range(0, len(unprocessed_ids), CHUNK_SIZE):
            chunk_ids = unprocessed_ids[i : i + CHUNK_SIZE]

            # Generate exactly enough placeholders for the current chunk
            placeholders = ",".join("?" for _ in chunk_ids)
            query = f"SELECT id, title FROM jobs WHERE id IN ({placeholders})"

            cursor = await db.execute(query, chunk_ids)
            jobs_data = await cursor.fetchall()

            job_ids = [row[0] for row in jobs_data]
            titles = [row[1] if row[1] is not None else "" for row in jobs_data]

            embeddings = await asyncio.to_thread(
                embedder.encode,
                titles,
                batch_size=ENCODE_BATCH_SIZE,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )

            probs = classifier.predict_proba(embeddings)[:, 1]

            buffer = [(job_id, bool(prob >= threshold)) for job_id, prob in zip(job_ids, probs)]

            await save_matches(db, buffer)
            await db.commit()

            total_processed += len(chunk_ids)
            print(f"Processed: {total_processed:,} / {len(unprocessed_ids):,} jobs")

    print(f"\nProcessing complete. Total jobs classified: {total_processed:,}")


if __name__ == "__main__":
    asyncio.run(run())
