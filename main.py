import asyncio
from typing import Any

import asyncpg
import joblib
from sentence_transformers import SentenceTransformer

PG_DSN = "postgresql://postgres:password@localhost:5432/dorker_db"

def filter_job_description_optimized(raw_text: str, clf: Any, embedder: Any) -> str:
    blocks = [b.strip() for b in raw_text.split('\n\n') if b.strip()]
    vectors = embedder.encode(blocks)

    # Get probabilities for all classes
    probs = clf.predict_proba(vectors)
    classes = clf.classes_

    # Get indices for the classes we want to keep
    req_idx = list(classes).index("REQUIREMENTS")
    resp_idx = list(classes).index("RESPONSIBILITIES")
    comp_idx = list(classes).index("COMPENSATION_LOCATION")

    filtered_blocks = []

    for i, block in enumerate(blocks):
        # If the combined probability of our KEEP classes is greater than 0.35
        # (Lowering the threshold from the default 0.50 to favor Recall)
        keep_prob = probs[i][req_idx] + probs[i][resp_idx] + probs[i][comp_idx]

        if keep_prob >= 0.35:
            filtered_blocks.append(block)

    return "\n\n".join(filtered_blocks)


async def fetch_matching_raw_jobs(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    query = """
        WITH matched_jobs AS (
            SELECT 
                j.id,
                ts_rank_cd(j.searchable, query) AS rank_score
            FROM jobs AS j,
                 websearch_to_tsquery('simple', $1) AS query
            WHERE j.searchable @@ query
        )
        SELECT
            j.id,
            j.title,
            j.description
        FROM matched_jobs AS m
        JOIN jobs AS j ON j.id = m.id
        WHERE j.posted_at >= NOW() - INTERVAL '1 month'
          AND j.location ~* $2
        ORDER BY m.rank_score DESC, j.posted_at DESC
        LIMIT 10;
    """

    fts_query = (
        '(go OR golang OR python OR "c#" OR ".net" OR dotnet OR "asp.net" OR "c++") '
        '-lead -principal -staff -director -architect -manager -vp -head -executive '
        '-frontend -"front end" -ui -ios -android -flutter -"react native" -php -wordpress -magento -"ruby on rails" -"network engineer"'
    )
    location_regex = r"(Ukraine|Europe|Remote|EMEA|Worldwide|Global)"

    async with pool.acquire() as conn:
        records = await conn.fetch(query, fts_query, location_regex)
        return records


async def run()-> None:
    async with asyncpg.create_pool(PG_DSN) as pool:
        jobs = await fetch_matching_raw_jobs(pool)

    # Load the trained Logistic Regression model
    clf = joblib.load("block_classifier.pkl")

    # Load the same embedding model used during training
    embedder = SentenceTransformer('all-MiniLM-L6-v2')

    for job in jobs:
        # Example usage
        filtered_text = filter_job_description_optimized(job["description"], clf, embedder)
        print("============================\n")
        print(filtered_text)
    print("============================\n")

if __name__ == "__main__":
    asyncio.run(run())