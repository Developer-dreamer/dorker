import asyncio
import logging
import re
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Literal, Optional, cast
from uuid import UUID

import aiohttp
import asyncpg
import joblib
import uuid6
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sentence_transformers import SentenceTransformer

from src.analytics.models import Analytics, MatchedJob, SuitabilityTier

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
PG_DSN = "postgresql://postgres:password@localhost:5432/dorker_db"

PIPELINE_VERSION = "v0.1.2"
ITERATION = 2

PERKS_SIGNAL_PATTERN = re.compile(
    r"(?i)\b("
    r"remote|hybrid|on[- ]?site|homeoffice|office|co[- ]?working|"
    r"travel|relocat|visa|sponsor|citizenship|clearance|"
    r"timezone|overlap|est|pst|utc|gmt|emea|latam|apac|"
    r"b2b|contractor|fop|w2|c2c"
    r")\b"
)


def filter_job_description_optimized(raw_text: str, clf: Any, embedder: Any) -> str:
    blocks = [b.strip() for b in raw_text.split("\n\n") if b.strip()]
    if not blocks:
        return ""

    vectors = embedder.encode(blocks)
    probs = clf.predict_proba(vectors)
    classes = list(clf.classes_)

    req_idx = classes.index("REQUIREMENTS")
    resp_idx = classes.index("RESPONSIBILITIES")
    comp_idx = classes.index("COMPENSATION_LOCATION")
    perk_idx = classes.index("BENEFITS_PERKS")

    filtered_blocks = []

    for i, block in enumerate(blocks):
        # 1. First block guard: Always keep if it contains basic title/metadata
        if i == 0 and len(block) < 300:
            filtered_blocks.append(block)
            continue

        core_prob = probs[i][req_idx] + probs[i][resp_idx] + probs[i][comp_idx]
        perk_prob = probs[i][perk_idx]

        # 2. Keep core functional blocks
        if core_prob >= 0.35:
            filtered_blocks.append(block)
            continue

        # 3. Conditionally keep BENEFITS_PERKS only if operational cues exist
        if perk_prob >= 0.35 and PERKS_SIGNAL_PATTERN.search(block):
            filtered_blocks.append(block)

    return "\n\n".join(filtered_blocks)


async def fetch_matching_raw_jobs(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    query = """
        SELECT j.id,
                j.title,
                j.location,
                c.name,
                j.description,
                j.salary_min,
                j.salary_max,
                j.salary_currency
            FROM jobs j
                CROSS JOIN websearch_to_tsquery('simple',
                                        '(go OR golang OR python OR "c#" OR ".net" OR dotnet OR "asp.net" OR "c++") '
                                            '-lead -principal -staff -director -architect -manager -vp -head -executive '
                                            '-frontend -"front end" -ui -ios -android -flutter -"react native" -php -wordpress -magento -"ruby on rails" -"network engineer"'

                                        ) AS query
                    JOIN companies AS c ON j.company_id = c.id
           WHERE EXISTS (
                SELECT 1
                FROM matches m
                WHERE m.job_id = j.id AND suitability_tier = 'SUITABLE' AND model = 'llama3.1' AND version = 'v0.0.0'
            )
            AND j.searchable @@ query
            AND j.posted_at >= NOW() - INTERVAL '1 month'
            AND j.location ILIKE ANY (ARRAY ['%Ukraine%', '%Europe%', '%Remote%', '%EMEA%', '%Worldwide%', '%Global%'])
            ORDER BY ts_rank_cd(j.searchable, query) DESC,
                    j.posted_at DESC;
    """

    async with pool.acquire() as conn:
        records = await conn.fetch(query)
        return cast(list[asyncpg.Record], records)


async def fetch_golden_set(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    query = """
            SELECT j.id,
                    j.title,
                    j.location,
                    j.description,
                    j.salary_min,
                    j.salary_max,
                    j.salary_currency
                FROM jobs j
               WHERE EXISTS (
                    SELECT 1
                    FROM jobs_fact_sheets jfs
                    WHERE jfs.job_id = j.id AND jfs.model = 'golden_set_manual'
                )
                ORDER BY j.posted_at DESC;
        """

    async with pool.acquire() as conn:
        records = await conn.fetch(query)
        return cast(list[asyncpg.Record], records)


class JobFactSheet(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # =========================================================================
    # PHASE 0: Identifiers
    # =========================================================================
    id: UUID = Field(default_factory=uuid6.uuid7)
    job_id: str

    # =========================================================================
    # PHASE 1: Concrete Technical Grounding (Literal token extractions)
    # =========================================================================
    primary_backend_languages: list[str] = Field(
        default_factory=list,
        description="Primary programming languages required for daily backend development (e.g., Go, Python, C#).",
    )
    secondary_tools: list[str] = Field(
        default_factory=list,
        description="Databases, infrastructure, cloud providers, and libraries (e.g., PostgreSQL, Docker, Redis, GCP, AWS).",
    )
    min_years_experience: int | None = Field(
        default=None,
        description=(
            "Absolute lowest required commercial years of experience for the primary stack. "
            "Set to null if unstated, junior, or internship."
        ),
    )
    is_experience_flexible: bool = Field(
        default=False,
        description=(
            "True if description states 'open to various experience levels', 'apply anyway', "
            "or implies flexible qualifications despite title."
        ),
    )

    # =========================================================================
    # PHASE 2: Operational Signals & Explicit Flags
    # =========================================================================
    is_legacy_maintenance: bool = Field(
        default=False,
        description=(
            "True ONLY if the role primarily maintains or extends legacy systems (PHP, older Java). "
            "Set to false if the role is migrating FROM legacy systems to modern stacks."
        ),
    )
    is_pure_network_or_systems: bool = Field(
        default=False,
        description="True ONLY if the core focus is hardware networking, routing protocols (BGP, OSPF), or telecom.",
    )
    has_mandatory_travel: bool = Field(
        default=False,
        description="True if regular in-person attendance, hardware pickup, or frequent travel is mandatory.",
    )
    has_uncompensated_oncall: bool = Field(
        default=False,
        description="True if on-call rotation is required without explicit compensation parameters.",
    )
    detected_operational_cues: list[str] = Field(
        default_factory=list,
        description="Exact linguistic cues indicating management debt (e.g., 'fast-paced environment', 'firefighting').",
    )

    # =========================================================================
    # PHASE 3: Location & Jurisdiction Details (Extractive tokens)
    # =========================================================================
    workplace_type: Literal["REMOTE", "HYBRID", "ON_SITE", "UNKNOWN"] = Field(
        ...,
        description="Operational workplace model: REMOTE, HYBRID, or ON_SITE.",
    )
    office_location_city: Optional[str] = Field(
        default=None,
        description="Target office location/city if workplace_type is HYBRID or ON_SITE.",
    )
    target_jurisdiction: Optional[str] = Field(
        default=None,
        description=(
            "Country ISO code if DOMESTIC and clear country specified: US, UA, GB. Region code if "
            "legal region specified (e.g., EU). Set to null if not restricted to a single country/EU."
        ),
    )
    region: Optional[Literal["EMEA", "LATAM", "APAC", "AMER", "APJ", "CEE", "MENA", "SEA"]] = Field(
        default=None,
        description=(
            "Regional abbreviation of operational timezone requirement: EMEA, APAC, LATAM, etc. "
            "Must be null if not an operational timezone corridor."
        ),
    )

    # =========================================================================
    # PHASE 4: High-Level Classification Enums (Synthesis)
    # =========================================================================
    geographic_scope: Literal[
        "UNKNOWN",
        "DOMESTIC",
        "REGIONAL",
        "GLOBAL",
    ] = Field(
        ...,
        description=(
            "Geographic classification conditioned on target_jurisdiction and region. "
            "DOMESTIC if restricted to specific countries (US only, EU only), tax forms, or clearance. "
            "GLOBAL if open worldwide with no country/bloc mandate. "
            "REGIONAL if bound to operational timezones (EMEA, LATAM, APAC)."
        ),
    )
    job_family: Literal[
        "BACKEND",
        "FRONTEND",
        "FULLSTACK",
        "QA_SDET",
        "DEVOPS_PLATFORM",
        "DATA_AI",
        "MOBILE",
        "NON_TECHNICAL",
        "OTHER",
    ] = Field(
        ...,
        description=(
            "Final classification of role alignment. Must be consistent with the "
            "extracted primary_backend_languages, secondary_tools, and responsibilities above."
        ),
    )

    # =========================================================================
    # Deserialization Sanitizers
    # =========================================================================
    @field_validator(
        "target_jurisdiction",
        "region",
        "office_location_city",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str):
            v_clean = v.strip()
            if v_clean == "" or v_clean.lower() in {"null", "none", "n/a"}:
                return None
            return v_clean
        return v


# Candidate skill profile definitions for matching
CANDIDATE_PRIMARY_LANGUAGES = {"go", "golang", "python", "c#", ".net", "dotnet", "c++", "c"}
CANDIDATE_SECONDARY_TOOLS = {
    "postgresql",
    "postgres",
    "docker",
    "linux",
    "git",
    "terraform",
    "gcp",
    "redis",
    "ef core",
    "entity framework",
    "opentelemetry",
    "webrtc",
    "firestore",
    "rest",
    "grpc",
}


def fact_sheet_to_match(sheet: JobFactSheet, raw_job_title: str) -> MatchedJob:
    """
    Deterministically evaluates an extracted JobFactSheet against candidate
    hard gates, technical capabilities, and strategic scoring rules.
    """
    pros: list[str] = []
    cons: list[str] = []
    warnings: list[str] = []

    # -------------------------------------------------------------------------
    # Step 1: Hard Gates (Fatal Constraints -> Immediate REJECTED)
    # -------------------------------------------------------------------------

    # 1.1 Geographic & Legal Authorization Gate
    if sheet.geographic_scope == "DOMESTIC" and sheet.target_jurisdiction != "UA":
        return MatchedJob(
            suitability_tier=SuitabilityTier.REJECTED,
            rejection_reason="Strict domestic residency, W-2 only, or citizenship/clearance required.",
            confidence_score=0.95,
            analytics=Analytics(warnings=["Geographic restriction / domestic legal barrier."]),
        )

    # 1.2 Workplace Presence Gate
    if sheet.workplace_type == "ON_SITE":
        return MatchedJob(
            suitability_tier=SuitabilityTier.REJECTED,
            rejection_reason="Mandatory 100% on-site office presence required.",
            confidence_score=0.95,
            analytics=Analytics(warnings=["Role does not support remote work."]),
        )

    if sheet.workplace_type == "HYBRID":
        city = (sheet.office_location_city or "").strip().lower()
        if "kyiv" not in city and "kiev" not in city:
            return MatchedJob(
                suitability_tier=SuitabilityTier.REJECTED,
                rejection_reason=f"Hybrid attendance required outside Kyiv ({sheet.office_location_city or 'Unknown location'}).",
                confidence_score=0.90,
                analytics=Analytics(
                    warnings=[f"Hybrid office location: {sheet.office_location_city}"]
                ),
            )

    # 1.3 Mandatory Travel Gate
    if sheet.has_mandatory_travel:
        return MatchedJob(
            suitability_tier=SuitabilityTier.REJECTED,
            rejection_reason="Mandatory travel or physical hardware pickup required.",
            confidence_score=0.90,
            analytics=Analytics(warnings=["Frequent travel / physical onboarding requirement."]),
        )

    # 1.4 Out-of-Scope Architecture / Legacy Maintenance Gate
    if sheet.is_legacy_maintenance:
        return MatchedJob(
            suitability_tier=SuitabilityTier.REJECTED,
            rejection_reason="Role primarily focused on legacy monolith maintenance (PHP / older Java).",
            confidence_score=0.95,
            analytics=Analytics(cons=["Legacy stack maintenance."]),
        )

    if sheet.is_pure_network_or_systems:
        return MatchedJob(
            suitability_tier=SuitabilityTier.REJECTED,
            rejection_reason="Pure network engineering / hardware routing focus (BGP, OSPF).",
            confidence_score=0.95,
            analytics=Analytics(cons=["Hardware/routing engineering focus."]),
        )

    # -------------------------------------------------------------------------
    # Step 2: Technical Capability Score Evaluation (Base: 1.0)
    # -------------------------------------------------------------------------
    tech_score = 1.0

    # 2.1 Seniority & Experience Penalties
    yoe = sheet.min_years_experience
    title_lower = raw_job_title.lower()
    is_senior_title = any(
        kw in title_lower for kw in ["senior", "snr", "lead", "principal", "staff"]
    )

    if yoe is not None:
        if yoe >= 5:
            if sheet.is_experience_flexible:
                tech_score -= 0.20
                cons.append(f"Senior level requested ({yoe}+ YoE), but marked flexible.")
            else:
                tech_score -= 0.35
                cons.append(f"Senior experience gap ({yoe}+ YoE required vs <1 yr commercial).")
        elif yoe >= 2:
            tech_score -= 0.10 if sheet.is_experience_flexible else 0.20
            cons.append(f"Middle experience requirement ({yoe}+ YoE vs <1 yr commercial).")
    else:
        if is_senior_title:
            if sheet.is_experience_flexible:
                tech_score -= 0.20
                cons.append("Title indicates Senior level, but text implies flexibility.")
            else:
                tech_score -= 0.35
                cons.append("Implicit Senior gap: Title is Senior, no numerical YoE stated.")
        else:
            if sheet.is_experience_flexible:
                pros.append("Flexible experience requirements stated in posting.")

    # 2.2 Primary Backend Language Alignment
    req_langs = [lang.strip().lower() for lang in sheet.primary_backend_languages if lang.strip()]
    matched_langs = [
        lang for lang in req_langs if any(c in lang for c in CANDIDATE_PRIMARY_LANGUAGES)
    ]

    if req_langs:
        if not matched_langs:
            tech_score -= 0.40
            cons.append(
                f"Primary language mismatch: requires {', '.join(sheet.primary_backend_languages)}."
            )
        else:
            pros.append(f"Direct match on primary language(s): {', '.join(matched_langs)}.")
            unmatched_langs = [lang for lang in req_langs if lang not in matched_langs]
            if unmatched_langs:
                tech_score -= min(0.20, 0.10 * len(unmatched_langs))
                cons.append(f"Secondary language gap: {', '.join(unmatched_langs)}.")
    else:
        tech_score -= 0.10
        warnings.append("No explicit primary backend language identified in posting.")

    # 2.3 Secondary Tools & Infrastructure Alignment
    req_tools = [t.strip().lower() for t in sheet.secondary_tools if t.strip()]
    matched_tools = [t for t in req_tools if any(c in t for c in CANDIDATE_SECONDARY_TOOLS)]
    unmatched_tools = [t for t in req_tools if not any(c in t for c in CANDIDATE_SECONDARY_TOOLS)]

    if matched_tools:
        pros.append(f"Tooling overlap: {', '.join(matched_tools[:5])}.")
    if unmatched_tools:
        tool_deduction = min(0.20, 0.05 * len(unmatched_tools))
        tech_score -= tool_deduction
        cons.append(f"Tooling/Cloud gaps: {', '.join(unmatched_tools[:4])}.")

    if not req_langs and not req_tools:
        return MatchedJob(
            suitability_tier=SuitabilityTier.REJECTED,
            rejection_reason="Out-of-scope domain: No backend languages or infrastructure tools detected.",
            confidence_score=0.95,
            analytics=Analytics(
                warnings=["Non-technical/Sales/Management role detected (False Positive)."]
            ),
        )

    # -------------------------------------------------------------------------
    # Step 3: Strategic Value Score Evaluation (Base: 1.0)
    # -------------------------------------------------------------------------
    strategic_score = 1.0

    if sheet.workplace_type == "REMOTE":
        pros.append("100% remote work arrangement.")
    elif sheet.workplace_type == "HYBRID":
        pros.append("Hybrid role with office located in Kyiv.")

    if sheet.has_uncompensated_oncall:
        strategic_score -= 0.15
        warnings.append("On-call rotation required without explicit compensation parameters.")

    for cue in sheet.detected_operational_cues:
        warnings.append(f"Operational risk cue: '{cue}'.")

    # -------------------------------------------------------------------------
    # Step 4: Normalization & Tier Classification
    # -------------------------------------------------------------------------
    tech_score = max(0.0, min(1.0, round(tech_score, 2)))
    strategic_score = max(0.0, min(1.0, round(strategic_score, 2)))

    confidence = 0.95
    if sheet.geographic_scope == "UNKNOWN":
        confidence -= 0.10
    if sheet.workplace_type == "UNKNOWN":
        confidence -= 0.10
    if sheet.min_years_experience is None:
        confidence -= 0.05
    confidence = max(0.50, round(confidence, 2))

    if tech_score >= 0.60 and strategic_score >= 0.60:
        tier = SuitabilityTier.SUITABLE
        strategic_reason = "High alignment with core technical stack and work arrangement."
        rejection_reason = ""
    elif tech_score < 0.60 and strategic_score >= 0.60:
        tier = SuitabilityTier.STRETCH
        strategic_reason = (
            "High strategic value role with addressable technical or seniority stretch."
        )
        rejection_reason = ""
    elif tech_score >= 0.50 and strategic_score < 0.60:
        tier = SuitabilityTier.RUNWAY
        strategic_reason = (
            "Viable technical baseline, but lower architectural or operational alignment."
        )
        rejection_reason = ""
    else:
        tier = SuitabilityTier.REJECTED
        strategic_reason = ""
        rejection_reason = (
            "Combined technical capability and strategic score fell below viable thresholds."
        )

    return MatchedJob(
        technical_capability_score=tech_score,
        strategic_value_score=strategic_score,
        confidence_score=confidence,
        suitability_tier=tier,
        strategic_reason=strategic_reason,
        rejection_reason=rejection_reason,
        analytics=Analytics(pros=pros, cons=cons, warnings=warnings),
    )


INSERT_COLUMNS = (
    "id",
    "job_id",
    "job_family",
    "geographic_scope",
    "workplace_type",
    "office_location_city",
    "target_jurisdiction",
    "region",
    "min_years_experience",
    "is_experience_flexible",
    "primary_backend_languages",
    "secondary_tools",
    "is_legacy_maintenance",
    "is_pure_network_or_systems",
    "has_mandatory_travel",
    "has_uncompensated_oncall",
    "detected_operational_cues",
    "model",
    "version",
    "iteration",
)

INSERT_QUERY = f"""
    INSERT INTO jobs_fact_sheets ({", ".join(INSERT_COLUMNS)})
    VALUES ({", ".join(f"${i + 1}" for i in range(len(INSERT_COLUMNS)))});
"""


async def process_job(
    job_dict: dict[str, Any],
    prompt_template: str,
    session: aiohttp.ClientSession,
    conn: asyncpg.Connection,
) -> None:
    full_prompt = f"{prompt_template}\n\n<job_payload>\n{job_dict}\n</job_payload>"

    MODEL = "qwen2.5-coder:7b"
    payload = {
        "model": MODEL,
        "prompt": full_prompt,
        "format": JobFactSheet.model_json_schema(),
        "stream": False,
        "keep_alive": "30m",
        "options": {"num_ctx": 4096, "temperature": 0.0},
    }

    try:
        async with session.post("http://localhost:11434/api/generate", json=payload) as response:
            if response.status != 200:
                logger.error(f"Ollama API Error for Job {job_dict['id']}: {response.status}")
                return

            response_data = await response.json()

            eval_count = response_data.get("eval_count", 0)
            prompt_eval_count = response_data.get("prompt_eval_count", 0)

            eval_duration_s = response_data.get("eval_duration", 1) / 1e9
            prompt_duration_s = response_data.get("prompt_eval_duration", 1) / 1e9
            total_duration_s = response_data.get("total_duration", 1) / 1e9

            gen_tps = eval_count / eval_duration_s if eval_duration_s > 0 else 0
            prompt_tps = prompt_eval_count / prompt_duration_s if prompt_duration_s > 0 else 0

            logger.info(
                f"Job {job_dict['id'][:8]} processed | "
                f"Total: {total_duration_s:.1f}s | "
                f"Prompt TPS: {prompt_tps:.1f} (Tokens: {prompt_eval_count}) | "
                f"Gen TPS: {gen_tps:.1f} (Tokens: {eval_count})"
            )

            try:
                sheet = JobFactSheet.model_validate_json(response_data["response"])

                payload = {
                    **sheet.model_dump(),
                    "id": uuid6.uuid7(),
                    "job_id": job_dict["id"],
                    "model": MODEL,
                    "version": PIPELINE_VERSION,
                    "iteration": ITERATION,
                }

                await conn.execute(INSERT_QUERY, *(payload[col] for col in INSERT_COLUMNS))

            except ValidationError as e:
                logger.warning(
                    f"Schema validation failed for {job_dict['id'][:8]}: {e.error_count()} errors"
                )
                logger.error(f"Validation Details: {e.errors()}")
                logger.error(f"Raw LLM Output: {response_data.get('response')}")
            except JSONDecodeError as e:
                logger.warning(f"JSON Syntax failed for {job_dict['id'][:8]}: {e}")

    except Exception as e:
        logger.error(f"Request failed for {job_dict['id']}: {str(e)}")


async def run() -> None:
    logger.info("Starting local classification pipeline...")

    clf, embedder = (
        joblib.load("/Users/serafym/Developer/dorker.space/dorker/block_classifier_nomic.pkl"),
        SentenceTransformer(
            "nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True, local_files_only=True
        ),
    )
    embedder.max_seq_length = 5000

    async with asyncpg.create_pool(PG_DSN) as pool:
        jobs = await fetch_golden_set(pool)
        logger.info(f"Retrieved {len(jobs)} jobs from database.")

    ranking_prompt_path = ROOT / "prompt" / "job_fact_sheet_example.md"
    if not ranking_prompt_path.exists():
        logger.critical("Prompt file not found. Exiting.")
        exit(-1)

    prompt_template = ranking_prompt_path.read_text(encoding="utf-8")

    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        conn = await asyncpg.connect(PG_DSN)
        try:
            for job in jobs:
                print("\n")
                job_dict = dict(job)
                job_dict["description"] = filter_job_description_optimized(
                    job_dict["description"], clf, embedder
                )

                await process_job(job_dict, prompt_template, session, conn)
        finally:
            await conn.close()
            logger.info("Pipeline execution completed.")


if __name__ == "__main__":
    asyncio.run(run())
