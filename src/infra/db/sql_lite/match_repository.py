import json

import aiosqlite

from src.config.logger import Logger
from src.domain.model.match import MatchedJob


class MatchRepository:
    def __init__(self, logger: Logger, db_path: str):
        self.logger = logger
        self.db_path = db_path

    async def save_new_match(self, job_id: int, user_id: int, match: MatchedJob) -> None:
        query = """
                INSERT INTO matches (
                    job_id,
                    user_id,
                    is_match,
                    suitability_tier,
                    technical_capability_score,
                    strategic_value_score,
                    strategic_reason,
                    extracted_company,
                    extracted_title,
                    extracted_location_status,
                    analytics,
                    cv_modification_points,
                    tailored_cover_letter,
                    application_form_answers,
                    internal_analysis_cot,
                    internal_scoring_breakdown,
                ) VALUES (
                    :job_id,
                    :user_id,
                    :is_match,
                    :suitability_tier,
                    :technical_capability_score,
                    :strategic_value_score,
                    :strategic_reason,
                    :extracted_company,
                    :extracted_title,
                    :extracted_location_status,
                    :analytics,
                    :cv_modification_points,
                    :tailored_cover_letter,
                    :application_form_answers,
                    :internal_analysis_cot,
                    :internal_scoring_breakdown
                );
            """

        params = {
            "job_id": job_id,
            "user_id": user_id,
            "is_match": int(match.is_match),
            "suitability_tier": match.application_status.value,
            "technical_capability_score": match.technical_capability_score,
            "strategic_value_score": match.strategic_value_score,
            "strategic_reason": match.strategic_reason,
            "extracted_company": match.metadata.extracted_company,
            "extracted_title": match.metadata.extracted_title,
            "extracted_location_status": match.metadata.extracted_location_status,

            "analytics": match.analytics.model_dump_json(),
            "cv_modification_points": json.dumps(match.cv_modification_points),
            "tailored_cover_letter": match.tailored_cover_letter,
            "application_form_answers": json.dumps([
                ans.model_dump() for ans in match.application_form_answers
            ]),
            "internal_analysis_cot": match.internal_analysis_cot.model_dump_json(),
            "internal_scoring_breakdown": match.internal_scoring_breakdown.model_dump_json()
        }

        async with aiosqlite.connect(self.db_path) as db:
            try:
                self.logger.info("Executing query", query=query)

                await db.execute(query, params)
                await db.commit()

            except aiosqlite.IntegrityError as e:
                self.logger.error("Database integrity violation for: ", user_id=user_id,
                                  job_id=job_id,
                                  error=str(e))

    async def update_match_by_id(self) -> None:
        pass

    async def delete_match_by_id(self) -> None:
        pass
