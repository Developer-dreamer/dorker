import csv

from analytics.pipeline import rank, reject
from scraping.normalize_descriptions import normalize_one
from scraping.scraper import AshbyScraper


def run_mvp(input_csv: str, output_csv: str) -> None:
    with open(input_csv, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        row = next(reader)

    slug = (row.get("slug") or "").strip() or (row.get("name") or "").strip()

    scraper = AshbyScraper(slug)

    jobs = scraper.fetch()

    results = []

    fieldnames = [
        "title", "url", "description", "ai_relevant",
        "is_match", "technical_capability_score", "strategic_value_score",
        "application_status", "strategic_reason", "pros", "cons", "warnings"
    ]

    for job in jobs:
        job.description = scraper.get_description(job)
        job.description = normalize_one(job.description)

        ai_result = reject(job)

        rank_data = {
            "is_match": "",
            "technical_capability_score": "",
            "strategic_value_score": "",
            "application_status": "",
            "strategic_reason": "",
            "pros": "",
            "cons": "",
            "warnings": ""
        }

        if ai_result:
            ai_rank = rank(job)

            # Flatten the nested Pydantic model for CSV writing
            rank_data = {
                "is_match": ai_rank.is_match,
                "technical_capability_score": ai_rank.technical_capability_score,
                "strategic_value_score": ai_rank.strategic_value_score,
                "application_status": ai_rank.application_status.value, # Extract string from Enum
                "strategic_reason": ai_rank.strategic_reason,
                "pros": " | ".join(ai_rank.analytics.pros),
                "cons": " | ".join(ai_rank.analytics.cons),
                "warnings": " | ".join(ai_rank.analytics.warnings)
            }

        row_data = {
            "title": job.title,
            "url": str(job.url),
            "description": job.description,
            "ai_relevant": ai_result,
        }

        row_data.update(rank_data)

        results.append(row_data)

        if ai_result:
            break

    with open(output_csv, "w", newline="", encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

if __name__ == "__main__":
    print("hello world")
    run_mvp("ashbyhq.csv", "results.csv")
