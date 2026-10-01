import json
from typing import List

from asyncpg import Pool

from src.database.protocols import ApplicationPacketRepository
from src.shared.models import (
    ApplicationGeneratedResponse,
    ApplicationPacket,
    JobForAnalytics,
    MatchedJob,
    Material,
    Question,
    RuntimeVersion,
)


class ApplicationPacketRepositoryPostgres(ApplicationPacketRepository):
    def __init__(self, pool: Pool, runtime_ver: RuntimeVersion) -> None:
        self.runtime_version = runtime_ver
        self.pool = pool

    async def save_packet(self, packet: ApplicationPacket) -> None:
        columns = (
            "job_id",
            "match_id",
            "materials",
            "summary",
            "questions",
            "debug",
            "version",
            "iteration",
        )

        query = f"""
                    INSERT INTO applications ({", ".join(columns)})
                    VALUES ({", ".join(f"${i + 1}" for i in range(len(columns)))})
                    RETURNING id;
                """

        dumped = packet.model_dump(exclude_none=True)

        if packet.summary is None:
            dumped["summary"] = None
        # Serialize list to JSON string for Postgres JSON/JSONB/TEXT column
        if "materials" in dumped:
            dumped["materials"] = json.dumps(dumped["materials"])
        if "questions" in dumped:
            dumped["questions"] = json.dumps(dumped["questions"])

        # Optional: ensure match_id is a UUID or str if the DB driver complains
        if "match_id" in dumped:
            dumped["match_id"] = str(dumped["match_id"])

        payload = {
            **dumped,
            "version": self.runtime_version.version,
            "iteration": self.runtime_version.iteration,
        }

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, *(payload.get(col) for col in columns))
            if row:
                packet.id = row["id"]

    async def get_pending_packets(self, limit: int = 1) -> List[JobForAnalytics]:
        query = """
                SELECT j.id AS id,
                       j.title,
                       j.location,
                       j.description,
                       j.questions,
                       j.salary_min,
                       j.salary_max,
                       j.salary_currency,

                       m.id AS match_id,
                       m.job_id AS match_job_id,
                       m.technical_capability_score,
                       m.strategic_value_score,
                       m.suitability_tier,

                       pa.id AS application_id,
                       pa.materials
                FROM applications pa
                         JOIN jobs j ON j.id = pa.job_id
                         JOIN matches m ON m.id = pa.match_id
                WHERE pa.updated_at IS NULL
                  AND j.deleted_at IS NULL
                    LIMIT $1;
                """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, limit)
            jobs: List[JobForAnalytics] = []

            for row in rows:
                data = dict(row)

                job = JobForAnalytics.model_validate(data)

                job.match = MatchedJob(
                    id=data["match_id"],
                    job_id=data["match_job_id"],
                    technical_capability_score=data["technical_capability_score"],
                    strategic_value_score=data["strategic_value_score"],
                    suitability_tier=data["suitability_tier"],
                )

                # Handle both JSON-parsed dicts and raw JSON strings safely
                raw_materials = data["materials"] or []
                if isinstance(raw_materials, str):
                    try:
                        raw_materials = json.loads(raw_materials)
                    except json.JSONDecodeError:
                        raw_materials = []

                raw_questions = data["questions"] or []
                if isinstance(raw_questions, str):
                    try:
                        raw_questions = json.loads(raw_questions)
                    except json.JSONDecodeError:
                        raw_questions = []

                parsed_materials = [
                    Material.model_validate_json(m)
                    if isinstance(m, str)
                    else Material.model_validate(m)
                    for m in (raw_materials if isinstance(raw_materials, list) else [raw_materials])
                ]

                parsed_questions: list[Question] = []
                for q in raw_questions if isinstance(raw_questions, list) else [raw_questions]:
                    if isinstance(q, str):
                        try:
                            parsed_questions.append(Question.model_validate_json(q))
                        except Exception:
                            parsed_questions.append(Question(question=q, answer=None))
                    elif isinstance(q, dict):
                        parsed_questions.append(Question.model_validate(q))
                    elif isinstance(q, Question):
                        parsed_questions.append(q)

                job.application = ApplicationPacket(
                    id=data["application_id"],
                    job_id=job.id,
                    match_id=data["match_id"],
                    materials=parsed_materials,
                    questions=parsed_questions,
                )
                jobs.append(job)

            return jobs

    async def update_packet(self, packet_id: int, resp: ApplicationGeneratedResponse) -> None:
        query = """
                UPDATE applications
                SET 
                    cover_letter = $2,
                    questions = $3,
                    debug = COALESCE(debug, '{}'::jsonb) || COALESCE($4::jsonb, '{}'::jsonb),
                    updated_at = NOW()
                WHERE id = $1
                """

        serialized_questions = (
            json.dumps([q.model_dump() for q in resp.questions])
            if resp.questions is not None
            else None
        )

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                packet_id,
                resp.cover_letter,
                serialized_questions,
                resp.debug,
            )
