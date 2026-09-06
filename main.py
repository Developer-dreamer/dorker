import asyncio
import logging
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Literal

import aiohttp
import asyncpg
import joblib
import uuid6
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sentence_transformers import SentenceTransformer
from tqdm.asyncio import tqdm

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

PIPELINE_VERSION = 'v0.1.2'

def filter_job_description_optimized(raw_text: str, clf: Any, embedder: Any) -> str:
    blocks = [b.strip() for b in raw_text.split("\n\n") if b.strip()]
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
        return records


class JobFactSheet(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: uuid6.UUID = uuid6.uuid7()
    job_id: str
    # --- 1. Location & Legal Constraints ---
    job_family: Literal[
        'BACKEND',
        'FRONTEND',
        'FULLSTACK',
        'QA_SDET',
        'DEVOPS_PLATFORM',
        'DATA_AI',
        'MOBILE',
        'NON_TECHNICAL',
        'OTHER'
    ] = Field(
        ...,
        description=("Literal representing job alignment. Names speaks for themselves.")
    )
    geographic_scope: Literal[
        "UNKNOWN",
        "DOMESTIC",
        "REGIONAL"
        "GLOBAL",
    ] = Field(
        ...,
        description=(
            "Geographic classification. DOMESTIC if restricted to specific countries "
            "(e.g., US only), strict domestic tax forms (W-2 only), citizenship mandates, or clearance. "
            "GLOBAL if open to worldwide, Europe, Ukraine, or B2B/EOR arrangements. "
            "REGIONAL if fixed to specific hours alignment (APAC, EMEA, SEA, LATAM)."
        ),
    )
    workplace_type: Literal["REMOTE", "HYBRID", "ON_SITE", "UNKNOWN"] = Field(
        ...,
        description="Operational workplace model: REMOTE, HYBRID, or ON_SITE.",
    )
    office_location_city: str | None = Field(
        default=None,
        description="Target office location/city if workplace_type is HYBRID or ON_SITE.",
    )
    target_jurisdiction: str | None = Field(
        default=None,
        description=("Country ISO code if DOMESTIC and clear country specified: US, UA, GB. Region code if"
                     "legal region specified (e.g., EU). Leave empty if geographic_scope IS NOT 'DOMESTIC'")
    )
    region: str | None = Field(
        default=None,
        description=("Regional abbreviation of operational timezone requirement: EMEA, APAC, LATAM."
                     "Leave empty if geographic_scope IS NOT 'REGIONAL'")
    )
    timezone_overlap_requested: str | None = Field(
        default=None,
        description="Target timezone alignment if requested (e.g., 'EST', '4 hours US East overlap').",
    )

    # --- 2. Seniority & Experience ---

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

    # --- 3. Technology Stack & Architectural Focus ---

    primary_backend_languages: list[str] = Field(
        default_factory=list,
        description="Primary programming languages required for daily backend development (e.g., Go, Python, C#).",
    )
    secondary_tools: list[str] = Field(
        default_factory=list,
        description="Databases, infrastructure, cloud providers, and libraries (e.g., PostgreSQL, Docker, Redis, GCP, AWS).",
    )

    # --- 4. Operational Red Flags ---

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

    # --- 5. Optional info ---
    detected_operational_cues: list[str] = Field(
        default_factory=list,
        description="Exact linguistic cues indicating management debt (e.g., 'fast-paced environment', 'firefighting').",
    )


# Candidate skill profile definitions for matching
CANDIDATE_PRIMARY_LANGUAGES = {"go", "golang", "python", "c#", ".net", "dotnet", "c++", "c"}
CANDIDATE_SECONDARY_TOOLS = {
    "postgresql", "postgres", "docker", "linux", "git", 
    "terraform", "gcp", "redis", "ef core", "entity framework",
    "opentelemetry", "webrtc", "firestore", "rest", "grpc"
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
    if sheet.geographic_scope == "STRICT_DOMESTIC_ONLY":
        return MatchedJob(
            suitability_tier=SuitabilityTier.REJECTED,
            rejection_reason="Strict domestic residency, W-2 only, or citizenship/clearance required.",
            confidence_score=0.95,
            analytics=Analytics(warnings=["Geographic restriction / domestic legal barrier."])
        )

    # 1.2 Workplace Presence Gate
    if sheet.workplace_type == "ON_SITE":
        return MatchedJob(
            suitability_tier=SuitabilityTier.REJECTED,
            rejection_reason="Mandatory 100% on-site office presence required.",
            confidence_score=0.95,
            analytics=Analytics(warnings=["Role does not support remote work."])
        )

    if sheet.workplace_type == "HYBRID":
        city = (sheet.office_location_city or "").strip().lower()
        if "kyiv" not in city and "kiev" not in city:
            return MatchedJob(
                suitability_tier=SuitabilityTier.REJECTED,
                rejection_reason=f"Hybrid attendance required outside Kyiv ({sheet.office_location_city or 'Unknown location'}).",
                confidence_score=0.90,
                analytics=Analytics(warnings=[f"Hybrid office location: {sheet.office_location_city}"])
            )

    # 1.3 Mandatory Travel Gate
    if sheet.has_mandatory_travel:
        return MatchedJob(
            suitability_tier=SuitabilityTier.REJECTED,
            rejection_reason="Mandatory travel or physical hardware pickup required.",
            confidence_score=0.90,
            analytics=Analytics(warnings=["Frequent travel / physical onboarding requirement."])
        )

    # 1.4 Out-of-Scope Architecture / Legacy Maintenance Gate
    if sheet.is_legacy_maintenance:
        return MatchedJob(
            suitability_tier=SuitabilityTier.REJECTED,
            rejection_reason="Role primarily focused on legacy monolith maintenance (PHP / older Java).",
            confidence_score=0.95,
            analytics=Analytics(cons=["Legacy stack maintenance."])
        )

    if sheet.is_pure_network_or_systems:
        return MatchedJob(
            suitability_tier=SuitabilityTier.REJECTED,
            rejection_reason="Pure network engineering / hardware routing focus (BGP, OSPF).",
            confidence_score=0.95,
            analytics=Analytics(cons=["Hardware/routing engineering focus."])
        )

    # -------------------------------------------------------------------------
    # Step 2: Technical Capability Score Evaluation (Base: 1.0)
    # -------------------------------------------------------------------------
    tech_score = 1.0

    # 2.1 Seniority & Experience Penalties
    yoe = sheet.min_years_experience
    title_lower = raw_job_title.lower()
    is_senior_title = any(kw in title_lower for kw in ["senior", "snr", "lead", "principal", "staff"])

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
        # FALLBACK: If LLM couldn't find a number, but the title says "Senior"
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
    req_langs = [l.strip().lower() for l in sheet.primary_backend_languages if l.strip()]
    matched_langs = [l for l in req_langs if any(c in l for c in CANDIDATE_PRIMARY_LANGUAGES)]

    if req_langs:
        if not matched_langs:
            tech_score -= 0.40
            cons.append(f"Primary language mismatch: requires {', '.join(sheet.primary_backend_languages)}.")
        else:
            pros.append(f"Direct match on primary language(s): {', '.join(matched_langs)}.")
            unmatched_langs = [l for l in req_langs if l not in matched_langs]
            if unmatched_langs:
                tech_score -= min(0.20, 0.10 * len(unmatched_langs))
                cons.append(f"Secondary language gap: {', '.join(unmatched_langs)}.")
    else:
        # No explicit primary language extracted
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
            analytics=Analytics(warnings=["Non-technical/Sales/Management role detected (False Positive)."])
        )
    # -------------------------------------------------------------------------
    # Step 3: Strategic Value Score Evaluation (Base: 1.0)
    # -------------------------------------------------------------------------
    strategic_score = 1.0

    # 3.1 Work Arrangement Value
    if sheet.workplace_type == "REMOTE":
        pros.append("100% remote work arrangement.")
    elif sheet.workplace_type == "HYBRID":
        pros.append("Hybrid role with office located in Kyiv.")

    # 3.2 Timezone Alignment
    if sheet.timezone_overlap_requested:
        warnings.append(f"Timezone alignment requested: {sheet.timezone_overlap_requested}.")

    # 3.3 Operational Cues & Uncompensated On-Call
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

    # Confidence calculation based on extraction completeness
    confidence = 0.95
    if sheet.geographic_scope == "UNKNOWN":
        confidence -= 0.10
    if sheet.workplace_type == "UNKNOWN":
        confidence -= 0.10
    if sheet.min_years_experience is None:
        confidence -= 0.05
    confidence = max(0.50, round(confidence, 2))

    # Determine Suitability Tier
    if tech_score >= 0.60 and strategic_score >= 0.60:
        tier = SuitabilityTier.SUITABLE
        strategic_reason = "High alignment with core technical stack and work arrangement."
        rejection_reason = ""
    elif tech_score < 0.60 and strategic_score >= 0.60:
        tier = SuitabilityTier.STRETCH
        strategic_reason = "High strategic value role with addressable technical or seniority stretch."
        rejection_reason = ""
    elif tech_score >= 0.50 and strategic_score < 0.60:
        tier = SuitabilityTier.RUNWAY
        strategic_reason = "Viable technical baseline, but lower architectural or operational alignment."
        rejection_reason = ""
    else:
        tier = SuitabilityTier.REJECTED
        strategic_reason = ""
        rejection_reason = "Combined technical capability and strategic score fell below viable thresholds."

    return MatchedJob(
        technical_capability_score=tech_score,
        strategic_value_score=strategic_score,
        confidence_score=confidence,
        suitability_tier=tier,
        strategic_reason=strategic_reason,
        rejection_reason=rejection_reason,
        analytics=Analytics(
            pros=pros,
            cons=cons,
            warnings=warnings
        )
    )

async def process_job(
    job: asyncpg.Record,
    prompt_template: str,
    session: aiohttp.ClientSession,
    conn: asyncpg.Connection,
    insert_sheet_query: str,
    insert_match_query: str,
) -> None:
    job_dict = dict(job)
    full_prompt = f"{prompt_template}\n\n<job_payload>\n{job_dict}\n</job_payload>"

    MODEL = 'qwen2.5-coder:7b'
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
                logger.error(f"Ollama API Error for Job {job['id']}: {response.status}")
                return

            response_data = await response.json()

            # --- Extract Metrics ---
            eval_count = response_data.get("eval_count", 0)
            prompt_eval_count = response_data.get("prompt_eval_count", 0)

            # Ollama returns durations in nanoseconds (1e9 ns = 1 second)
            eval_duration_s = response_data.get("eval_duration", 1) / 1e9
            prompt_duration_s = response_data.get("prompt_eval_duration", 1) / 1e9
            total_duration_s = response_data.get("total_duration", 1) / 1e9

            gen_tps = eval_count / eval_duration_s if eval_duration_s > 0 else 0
            prompt_tps = prompt_eval_count / prompt_duration_s if prompt_duration_s > 0 else 0

            logger.info(
                f"Job {job['id'][:8]} processed | "
                f"Total: {total_duration_s:.1f}s | "
                f"Prompt TPS: {prompt_tps:.1f} (Tokens: {prompt_eval_count}) | "
                f"Gen TPS: {gen_tps:.1f} (Tokens: {eval_count})"
            )

            try:
                sheet = JobFactSheet.model_validate_json(response_data["response"])

                match = fact_sheet_to_match(sheet, job["title"])
                async with conn.transaction():
                    await conn.execute(
                        insert_sheet_query,
                        uuid6.uuid7(),
                        job["id"],
                        sheet.geographic_scope,
                        sheet.workplace_type,
                        sheet.office_location_city,
                        sheet.timezone_overlap_requested,
                        sheet.min_years_experience,
                        sheet.is_experience_flexible,
                        sheet.primary_backend_languages,
                        sheet.secondary_tools,
                        sheet.is_legacy_maintenance,
                        sheet.is_pure_network_or_systems,
                        sheet.has_mandatory_travel,
                        sheet.has_uncompensated_oncall,
                        sheet.detected_operational_cues,
                        MODEL
                    )
                    await conn.execute(
                        insert_match_query,
                        uuid6.uuid7(),
                        job["id"],
                        True,
                        match.suitability_tier,
                        "PENDING" if match.strategic_reason else "DECLINED",
                        match.technical_capability_score,
                        match.strategic_value_score,
                        match.confidence_score,
                        match.strategic_reason,
                        match.rejection_reason,
                        match.analytics.model_dump_json(),
                        match.internal_analysis_cot,
                        MODEL
                    )

            except ValidationError as e:
                logger.warning(
                    f"Schema validation failed for {job['id'][:8]}: {e.error_count()} errors"
                )
                logger.error(f"Validation Details: {e.errors()}")
                logger.error(f"Raw LLM Output: {response_data.get('response')}")
            except JSONDecodeError as e:
                logger.warning(f"JSON Syntax failed for {job['id'][:8]}: {e}")

    except Exception as e:
        logger.error(f"Request failed for {job['id']}: {str(e)}")


async def run() -> None:
    logger.info("Starting local classification pipeline...")

    async with asyncpg.create_pool(PG_DSN) as pool:
        jobs = await fetch_matching_raw_jobs(pool)
        logger.info(f"Retrieved {len(jobs)} jobs from database.")

    ranking_prompt_path = ROOT / "prompt" / "job_fact_sheet.md"
    if not ranking_prompt_path.exists():
        logger.critical("Prompt file not found. Exiting.")
        exit(-1)

    prompt_template = ranking_prompt_path.read_text(encoding="utf-8")

    insert_query = f"""INSERT INTO matches
                            (id, job_id, is_technical, suitability_tier, pipeline_status, 
                             technical_capability_score, strategic_value_score, confidence_score, 
                             strategic_reason, rejection_reason, analytics, internal_analysis_cot, version, model)
                      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, '{PIPELINE_VERSION}', $13)
                   """

    insert_sheet_query = f"""
                        INSERT INTO jobs_fact_sheets (
                            id,job_id,geographic_scope,workplace_type,office_location_city,timezone_overlap_requested,
                            min_years_experience,is_experience_flexible,primary_backend_languages,secondary_tools,
                            is_legacy_maintenance,is_pure_network_or_systems,has_mandatory_travel,
                            has_uncompensated_oncall,detected_operational_cues, model, version
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, '{PIPELINE_VERSION}'
                        );
                    """

    # Use aiohttp to prevent blocking the event loop
    timeout = aiohttp.ClientTimeout(total=300)  # 5 min timeout per job
    async with aiohttp.ClientSession(timeout=timeout) as session:
        conn = await asyncpg.connect(PG_DSN)
        try:
            # Using tqdm for a progress bar
            for job in jobs:
                print('\n')
                # job_dict["description"] = filter_job_description_optimized(job["description"], clf, embedder)

                await process_job(job, prompt_template, session, conn, insert_sheet_query, insert_query)
        finally:
            await conn.close()
            logger.info("Pipeline execution completed.")


if __name__ == "__main__":
    asyncio.run(run())
