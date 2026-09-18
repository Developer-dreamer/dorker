import time
from contextlib import contextmanager
from logging import Logger
from typing import Any, Generator, get_args

import guidance
import numpy as np
from guidance import gen, select
from guidance.models import LlamaCpp
from sentence_transformers import SentenceTransformer

from .database import JobFactSheetRepository, JobRepository
from .models import (
    DomainEntities,
    JobFactSheet,
    JobForAnalytics,
    LocationEntities,
    RedFlagsEntities,
    RuntimeVersion,
)


@contextmanager
def log_guidance_step(
    logger: Logger, job_id: str, step_name: str, lm_initial: LlamaCpp
) -> Generator[dict[str, Any], None, None]:
    start_time = time.perf_counter()
    initial_tokens = len(lm_initial)
    metrics = {"llm": lm_initial}

    try:
        yield metrics
    finally:
        elapsed = time.perf_counter() - start_time
        lm_final = metrics.get("llm", lm_initial)
        final_tokens = len(lm_final)

        generated_tokens = max(0, final_tokens - initial_tokens)
        gen_tps = (generated_tokens / elapsed) if elapsed > 0 else 0.0

        logger.info(
            f"[{job_id}] {step_name} completed in {elapsed:.2f}s | "
            f"Prompt: {initial_tokens} tok | Gen: {generated_tokens} tok ({gen_tps:.1f} tok/s) | "
            f"Total context: {final_tokens} tok"
        )


# Matches: one item without commas/newlines, followed by comma + next item
# Stops immediately when hitting a newline
open_csv_regex = r"[^,\n]+(, [^,\n]+)*"


class Engine:
    def __init__(
        self,
        logger: Logger,
        runtime_version: RuntimeVersion,
        job_repo: JobRepository,
        fact_sheet_repo: JobFactSheetRepository,
        llm: LlamaCpp,
        embedder: SentenceTransformer,
        clf: Any,
    ):
        self.logger = logger
        self.runtime_version = runtime_version
        self.job_repo = job_repo
        self.fact_sheet_repo = fact_sheet_repo

        self.llm = llm
        self.embedder = embedder
        self.clf = clf

    async def run(self) -> None:
        entries = await self.job_repo.get_matched_jobs()
        self.logger.info(f"Retrieved {len(entries)} candidate jobs from repository.")

        filtered_entries = await self._filter_descriptions(entries)

        for idx, job in enumerate(filtered_entries, start=1):
            if not job.description:
                continue

            job_start = time.perf_counter()
            self.logger.info(
                f"--- [{idx}/{len(filtered_entries)}] Processing job {job.id} ({job.title}) ---"
            )

            try:
                location = await self._retrieve_location(job)
                domain = await self._retrieve_domain_entities(job)
                red_flags = await self._retrieve_hidden_redflags(job)

                fact_sheet = JobFactSheet.from_llm_responses(job.id, location, domain, red_flags)
                await self.fact_sheet_repo.save_fact_sheet(fact_sheet)

                total_time = time.perf_counter() - job_start
                self.logger.info(
                    f"[{job.id}] Successfully saved | Total time: {total_time:.2f}s | "
                    f"Scope: {location.geographic_scope} | Family: {domain.job_family} | "
                    f"Exp: {domain.min_years_experience}y"
                )

            except Exception as e:
                self.logger.exception(f"[{job.id}] Pipeline extraction failed: {str(e)}")

    async def _retrieve_location(self, job: JobForAnalytics) -> LocationEntities:
        if not job.description_blocks:
            self.logger.warning("Unable to process job. No description blocks.")
            return LocationEntities()

        llm = self.llm

        description = f"Location: {job.location}\nDescription:\n"
        for key, _, text in job.description_blocks:
            if key in ["BENEFITS_PERKS", "COMPANY_PROFILE", "COMPENSATION_LOCATION"]:
                description += text

        location_entity = LocationEntities()
        with log_guidance_step(self.logger, str(job.id), "Location", llm) as tracker:
            with guidance.user():
                llm += f"""Analyze this job description:{description}.
                        Identify and save all entities that could be useful when
                        resolving workplace location: employment country, region, legal requirements
                        for an applicant.
                        """

            with guidance.assistant():
                step = 1
                llm += f"""Step {step} - Extract workplace. It is defined by next constraints:
                            - Remote: role does not require any office attendance at all, no probation period on-site, no "attend office to receive youre devices", no hybrid. Nothing. Pure remote availability.
                            - Hybrid: if role specified as remote, operational activity going on remote, however it requires 'device pick up' on-site - mark as hybrid. If role explicitly specifies hybrid, or Remote or requires regular office attendance - mark it as hybrid. EXCEPTION: role is remote, however requires traveling one-twice, etc. times per year.
                            - On-Site: role clearly specifies that all work is going on in office with no exceptions and alternatives.
                            - Unknown: no clear workplace type was specified anywhere in the description or location fields.
                            """
                llm += "Evidence from description: " + gen(name="ev_workplace", stop="\n") + "\n"
                options = list(get_args(LocationEntities.model_fields["workplace_type"].annotation))
                llm += "Selected workplace: " + select(options, name="workplace_type") + "\n\n"
                self.logger.info(
                    f"[{job.id}] Workplace Ev: '{llm['ev_workplace']}' -> {llm['workplace_type']}"
                )
                location_entity.workplace_type = llm["workplace_type"]
                step += 1

                if llm["workplace_type"] in ["ON-SITE", "HYBRID"]:
                    llm += f"""Step {step} - Extract role's scope. Identify, whether company
                                explicitly mentioned office location. If yes, single: output in 
                                format City, Country. If yes, multiple: output any, BUT prioritize
                                Kyiv, Ukraine if mentioned. Else leave empty string value.
                                """
                    llm += (
                        "Evidence from description: " + gen(name="ev_office_city", stop="\n") + "\n"
                    )
                    llm += (
                        "Selected office location: " + gen(name="office_location", stop="\n") + "\n"
                    )
                    if llm["office_location"]:
                        location_entity.office_location_city = llm["office_location"]
                    self.logger.info(
                        f"[{job.id}] Office Ev: '{llm['ev_office_city']}' -> {location_entity.office_location_city}"
                    )
                    step += 1

                llm += f"""Step {step} - Extract role's scope. It is defined by next constraints:
                            - DOMESTIC: Hiring is legally restricted to a single country OR a legally unified economic bloc requiring specific residency/work authorization (e.g., "US Only", "Must reside in the UK", "EU only", "Must have EU work permit").
                            - REGIONAL: Hiring is defined by timezone or broad geographical corridors with no single legal work permit required (e.g., "EMEA", "LATAM", "APAC", "Americas").
                            - GLOBAL: Hiring is explicitly open worldwide, anywhere, or via an Employer of Record (e.g., Deel/Remote.com) with no country/bloc restrictions.
                            - UNKNOWN: No clear geographic or legal constraints are stated.

                            <constraints>
                            Company specifying specific country code (Remote US, Remote U.K)
                            explicitly cannot be global, unless contradicted in description with
                            something like "hiring globally". Treat such cases as Domestic.
                            </constraints>
                            """
                llm += "Evidence from description: " + gen(name="ev_scope", stop="\n") + "\n"
                options = list(
                    get_args(LocationEntities.model_fields["geographic_scope"].annotation)
                )
                llm += (
                    "Selected geographic scope: "
                    + select(options, name="geographic_scope")
                    + "\n\n"
                )
                self.logger.info(
                    f"[{job.id}] Scope Ev: '{llm['ev_scope']}' -> {llm['geographic_scope']}"
                )
                location_entity.geographic_scope = llm["geographic_scope"]
                step += 1

                if llm["geographic_scope"] == "REGIONAL":
                    llm += f"Step {step} - Extract region name."
                    llm += "Evidence from description: " + gen(name="ev_region", stop="\n") + "\n"
                    options = list(get_args(LocationEntities.model_fields["region"].annotation))
                    llm += "Selected  region: " + select(options, name="region") + "\n\n"

                    location_entity.region = llm["region"]
                    self.logger.info(
                        f"[{job.id}] Region Ev: '{llm['ev_region']}' -> {llm['region']}"
                    )

                elif llm["geographic_scope"] == "DOMESTIC":
                    llm += f'Step {step} - Extract jurisdiction. It is defined as 2-letter ISO 3166-1 Alpha-2 code (e.g., "US", "GB", "UA").'
                    llm += (
                        "Evidence from description: "
                        + gen(name="ev_jurisdiction", stop="\n")
                        + "\n"
                    )
                    llm += (
                        "Selected jurisdiction: "
                        + gen(regex=r"[A-Z]{2}", name="target_jurisdiction")
                        + "\n"
                    )

                    location_entity.target_jurisdiction = llm["target_jurisdiction"]
                    self.logger.info(
                        f"[{job.id}] Jurisdiction Ev: '{llm['ev_jurisdiction']}' -> {llm['target_jurisdiction']}"
                    )
            tracker["llm"] = llm

        return location_entity

    async def _retrieve_domain_entities(self, job: JobForAnalytics) -> DomainEntities:
        llm = self.llm
        if not job.description_blocks:
            self.logger.warning("Unable to process job. No description blocks.")
            return DomainEntities()

        description = f"Title: {job.title}\nDescription:\n"
        for key, _, text in job.description_blocks:
            if key in ["REQUIREMENTS", "RESPONSIBILITIES"]:
                description += text

        domain_entity = DomainEntities()

        with log_guidance_step(self.logger, str(job.id), "Domain", llm) as tracker:
            with guidance.user():
                llm += f"""Analyze this job description:{description}.
                        Identify and save all entities that could be required
                        """

            with guidance.assistant():
                step = 1
                llm += f"""Step {step} - Extract primary backend languages.
                        They are defined as the ones, that are used by the company for
                        backend development. Counter-example: if python mentioned,
                        but the role requires it for scripting, or ML trainings,
                        it is not the primary backend language.
                        """
                llm += (
                    "Primary backend languages mentioned: "
                    + gen(regex=open_csv_regex, name="primary_backend_languages", stop="\n")
                    + "\n"
                )
                langs_pure = llm["primary_backend_languages"].split(",")
                domain_entity.primary_backend_languages = [lang.strip() for lang in langs_pure]
                step += 1

                llm += f"""Step {step} - Extract secondary tools.
                        They are defined as tools/technologies/languages, that are required in day to day work,
                        but failed to get into primary backend languages. Omit architectural patterns.
                        E.g.: description = "You will be working with LangChain, RAG" -> LangChain.
                        """
                llm += (
                    "Secondary tools mentioned: "
                    + gen(regex=open_csv_regex, name="secondary_tools", stop="\n")
                    + "\n"
                )
                tools_pure = llm["secondary_tools"].split(",")
                domain_entity.secondary_tools = [tool.strip() for tool in tools_pure]
                step += 1

                llm += f"""Step {step} - Extract minimal years of experience required.
                        Examples (description given -> desired output):
                            "Requirements: 3-5 years of experience with Python" -> 3
                            "Requirements: 5 years of development experience, 3 years of experience in production" -> 3
                            "Requirements: 3 years of experience with python 4 with TypeScript, 2 with Kubernetes" -> 2
                            "Requirements: proficient with python" -> 0
                        0 means unspecified.
                        """
                llm += "Evidence from description: " + gen(name="ev_evidence", stop="\n") + "\n"
                llm += (
                    "Minimal years of experience required: "
                    + gen(regex=r"0|[1-9][0-9]?", name="minimal_years_experience", stop="\n")
                    + "\n"
                )
                domain_entity.min_years_experience = (
                    llm["minimal_years_experience"]
                    if int(llm["minimal_years_experience"]) > 0
                    else None
                )
                step += 1

                llm += f"""Step {step} - Identify if experience flexible.
                        Sometimes companies specify years of experience, but later mention in description
                        "you shouldn't meet all of requirements", "apply even if you don't check out every
                        box". If description explicitly allows to negotiate final level of experience,
                        set to true.
                        """
                llm += "Evidence from description: " + gen(name="ev_flexibility", stop="\n") + "\n"
                llm += (
                    "Is experience flexible? "
                    + select(["true", "false"], name="flexibility")
                    + "\n"
                )
                domain_entity.is_experience_flexible = llm["flexibility"] == "true"
                step += 1

                llm += f"""Step {step} - Identify job family. It is defined by next constraints:
                        - Backend: role is backend focused only (e.g., API development, Infrastructure building). Tooling mention permitted (building CLI, configs, etc.) unless primary focus lies in backend perspecite
                        - Frontend: role is frontend focuesd (e.g. UI, UX). Design mention is permitted unless it requires coding.
                        - Full-Stack: the role combines both backend and frontend ("Go + JS", "Python + React", etc.).
                        - QA_SDET (stands for Quality assurance or Software development in Test): if role's primarily focus on testing, designing testing pipelines, UI testing, manual, etc.
                        - Devops/platform: if role's primarily focus on configuration, cloud management, CI/CD pipelines. If role requires only basic scripting with python, bash, etc. however keeping focus as Devops - mark as Devops.
                        - DATA_AI: strictly for Software AI/ML (NLP, LLMs, Computer Vision, Recommenders, tabular business ML). Natural sciences (Bioinformatics, Computational Chemistry, Geophysics, Biostatistics, Quantum Mechanics) MUST be classified as OTHER
                        - Mobile: if role's primary focus is mobile or desktop development (React Native, Swift, Java, Kotlin, etc.) or it is any of other types, however mentiones in requirements commitment to mobile development (e.g.: "This is backend role, however you're required to maintain our Kotlin app...").
                        - Non-technical: If the primary deliverable is Sales, Customer Support, Regulatory/Financial Compliance, Teaching, or Survey Operations—even if it requires SQL, Python scripting, or technical knowledge—classify as NON_TECHNICAL.
                        - Other: if none of listed above criterias matched - keep Other.
                          <critical_constraint>
                          - A role is BACKEND if the work involves building web services, databases, orchestrators, or APIs, even if the payload or product involves LLMs, agents, or AI. It is only DATA_AI if the engineer's primary duty is developing, training, fine-tuning, or mathematically evaluating model weights.
                          </critical_constraint>
                        """
                llm += "Evidence from description: " + gen(name="ev_family", stop="\n") + "\n"
                options = list(get_args(DomainEntities.model_fields["job_family"].annotation))
                llm += "Selected job family: " + select(options, name="job_family") + "\n"
                domain_entity.job_family = llm["job_family"]

                self.logger.info(
                    f"[{job.id}] Domain: Family={domain_entity.job_family} | "
                    f"YoE={domain_entity.min_years_experience} | "
                    f"Langs={domain_entity.primary_backend_languages}"
                )

            tracker["llm"] = llm

        return domain_entity

    async def _retrieve_hidden_redflags(self, job: JobForAnalytics) -> RedFlagsEntities:
        llm = self.llm
        if not job.description_blocks:
            self.logger.warning("Unable to process job. No description blocks.")
            return RedFlagsEntities()

        description = f"Title: {job.title}\nDescription:\n"
        for key, _, text in job.description_blocks:
            if key in [
                "REQUIREMENTS",
                "RESPONSIBILITIES",
                "COMPANY_PROFILE",
                "COMPENSATION_LOCATION",
            ]:
                description += text

        red_flags = RedFlagsEntities()
        with log_guidance_step(self.logger, str(job.id), "RedFlags", llm) as tracker:
            with guidance.user():
                llm += f"""Analyze this job description:{description}"""

            with guidance.assistant():
                step = 1
                llm += f"""Step {step} - Identify if role requires legacy maintenance. 
                        It is defined by next constraints:
                        If role explicitly specifies "you will be supporting/maintaining our PHP monolith" or 
                        "you will be responsible for maintaining our python monolith". 
                        No new features delivery mentioned. Pure maintenance role.
                        """
                llm += "Evidence from description: " + gen(name="ev_legacy", stop="\n") + "\n"
                llm += (
                    "Is legacy maintenance required? "
                    + select([True, False], name="is_legacy")
                    + "\n"
                )
                red_flags.is_legacy_maintenance = llm["is_legacy"]
                step += 1

                llm += f"""Step {step} - Identify if role is pure networks or systems. 
                            It is defined by next constraints:
                            exists to differentiate backend developer engineering roles with core engineering roles. 
                            If role specifies that most of you're daily work will consist of network exploration, 
                            protocol building/debugging, without shipping features, 
                            focusing only on basic network/system engineering.
                            """
                llm += "Evidence from description: " + gen(name="ev_network", stop="\n") + "\n"
                llm += (
                    "Is pure network or systems role? "
                    + select([True, False], name="is_network")
                    + "\n"
                )
                red_flags.is_pure_network_or_systems = llm["is_network"]
                step += 1

                llm += f"""Step {step} - Identify if role requires travel. 
                                        Check if role explicitly states that mandatory travel required N-times per year/quartal/etc.
                                        """
                llm += "Evidence from description: " + gen(name="ev_travel", stop="\n") + "\n"
                llm += "Does require travel? " + select([True, False], name="is_travel") + "\n"
                red_flags.has_mandatory_travel = llm["is_travel"]
                step += 1

                llm += f"""Step {step} - Identify if role has oncall rotation. 
                        Check if role explicitly requires on call rotation.
                        """
                llm += "Evidence from description: " + gen(name="ev_oncall", stop="\n") + "\n"
                llm += "Does require travel? " + select([True, False], name="has_oncall") + "\n"
                red_flags.has_uncompensated_oncall = llm["has_oncall"]

                self.logger.info(
                    f"[{job.id}] RedFlags: Legacy={red_flags.is_legacy_maintenance} | "
                    f"NetSys={red_flags.is_pure_network_or_systems} | "
                    f"Travel={red_flags.has_mandatory_travel} | "
                    f"OnCall={red_flags.has_uncompensated_oncall}"
                )

            tracker["llm"] = llm

        return red_flags

    async def _filter_descriptions(self, jobs: list[JobForAnalytics]) -> list[JobForAnalytics]:
        flat_blocks: list[str] = []
        job_slices: list[slice] = []

        start_idx = 0
        for job in jobs:
            if not job.description:
                self.logger.warning("Empty description. Can't process with classification")
                return []
            blocks = [b.strip() for b in job.description.split("\n\n") if b.strip()]
            flat_blocks.extend(blocks)
            end_idx = start_idx + len(blocks)
            job_slices.append(slice(start_idx, end_idx))
            start_idx = end_idx

        if not flat_blocks:
            for job in jobs:
                job.description_blocks = []
            return jobs

        # 1. Batch encode and predict across all jobs
        all_vectors = self.embedder.encode(flat_blocks)
        all_probs = self.clf.predict_proba(all_vectors)
        classes = list(self.clf.classes_)

        # 2. Assign (top_class, top_proba, text) to every block
        for job, s in zip(jobs, job_slices, strict=True):
            job_blocks = flat_blocks[s]
            probs = all_probs[s]

            if len(job_blocks) == 0:
                job.description_blocks = []
                continue

            top_indices = np.argmax(probs, axis=1)

            job.description_blocks = [
                (classes[class_idx], float(probs[i, class_idx]), block)
                for i, (class_idx, block) in enumerate(zip(top_indices, job_blocks, strict=True))
            ]

        return jobs


# def fact_sheet_to_match(sheet: JobFactSheet, raw_job_title: str) -> MatchedJob:
#     """
#     Deterministically evaluates an extracted JobFactSheet against candidate
#     hard gates, technical capabilities, and strategic scoring rules.
#     """
#     pros: list[str] = []
#     cons: list[str] = []
#     warnings: list[str] = []
#
#     # -------------------------------------------------------------------------
#     # Step 1: Hard Gates (Fatal Constraints -> Immediate REJECTED)
#     # -------------------------------------------------------------------------
#
#     # 1.1 Geographic & Legal Authorization Gate
#     if sheet.geographic_scope == "DOMESTIC" and sheet.target_jurisdiction != "UA":
#         return MatchedJob(
#             suitability_tier=SuitabilityTier.REJECTED,
#             rejection_reason="Strict domestic residency, W-2 only, or citizenship/clearance required.",
#             confidence_score=0.95,
#             analytics=Analytics(warnings=["Geographic restriction / domestic legal barrier."]),
#         )
#
#     # 1.2 Workplace Presence Gate
#     if sheet.workplace_type == "ON_SITE":
#         return MatchedJob(
#             suitability_tier=SuitabilityTier.REJECTED,
#             rejection_reason="Mandatory 100% on-site office presence required.",
#             confidence_score=0.95,
#             analytics=Analytics(warnings=["Role does not support remote work."]),
#         )
#
#     if sheet.workplace_type == "HYBRID":
#         city = (sheet.office_location_city or "").strip().lower()
#         if "kyiv" not in city and "kiev" not in city:
#             return MatchedJob(
#                 suitability_tier=SuitabilityTier.REJECTED,
#                 rejection_reason=f"Hybrid attendance required outside Kyiv ({sheet.office_location_city or 'Unknown location'}).",
#                 confidence_score=0.90,
#                 analytics=Analytics(
#                     warnings=[f"Hybrid office location: {sheet.office_location_city}"]
#                 ),
#             )
#
#     # 1.3 Mandatory Travel Gate
#     if sheet.has_mandatory_travel:
#         return MatchedJob(
#             suitability_tier=SuitabilityTier.REJECTED,
#             rejection_reason="Mandatory travel or physical hardware pickup required.",
#             confidence_score=0.90,
#             analytics=Analytics(warnings=["Frequent travel / physical onboarding requirement."]),
#         )
#
#     # 1.4 Out-of-Scope Architecture / Legacy Maintenance Gate
#     if sheet.is_legacy_maintenance:
#         return MatchedJob(
#             suitability_tier=SuitabilityTier.REJECTED,
#             rejection_reason="Role primarily focused on legacy monolith maintenance (PHP / older Java).",
#             confidence_score=0.95,
#             analytics=Analytics(cons=["Legacy stack maintenance."]),
#         )
#
#     if sheet.is_pure_network_or_systems:
#         return MatchedJob(
#             suitability_tier=SuitabilityTier.REJECTED,
#             rejection_reason="Pure network engineering / hardware routing focus (BGP, OSPF).",
#             confidence_score=0.95,
#             analytics=Analytics(cons=["Hardware/routing engineering focus."]),
#         )
#
#     # -------------------------------------------------------------------------
#     # Step 2: Technical Capability Score Evaluation (Base: 1.0)
#     # -------------------------------------------------------------------------
#     tech_score = 1.0
#
#     # 2.1 Seniority & Experience Penalties
#     yoe = sheet.min_years_experience
#     title_lower = raw_job_title.lower()
#     is_senior_title = any(
#         kw in title_lower for kw in ["senior", "snr", "lead", "principal", "staff"]
#     )
#
#     if yoe is not None:
#         if yoe >= 5:
#             if sheet.is_experience_flexible:
#                 tech_score -= 0.20
#                 cons.append(f"Senior level requested ({yoe}+ YoE), but marked flexible.")
#             else:
#                 tech_score -= 0.35
#                 cons.append(f"Senior experience gap ({yoe}+ YoE required vs <1 yr commercial).")
#         elif yoe >= 2:
#             tech_score -= 0.10 if sheet.is_experience_flexible else 0.20
#             cons.append(f"Middle experience requirement ({yoe}+ YoE vs <1 yr commercial).")
#     else:
#         if is_senior_title:
#             if sheet.is_experience_flexible:
#                 tech_score -= 0.20
#                 cons.append("Title indicates Senior level, but text implies flexibility.")
#             else:
#                 tech_score -= 0.35
#                 cons.append("Implicit Senior gap: Title is Senior, no numerical YoE stated.")
#         else:
#             if sheet.is_experience_flexible:
#                 pros.append("Flexible experience requirements stated in posting.")
#
#     # 2.2 Primary Backend Language Alignment
#     req_langs = [lang.strip().lower() for lang in sheet.primary_backend_languages if lang.strip()]
#     matched_langs = [lang for lang in req_langs if any(c in lang for c in [])]
#
#     if req_langs:
#         if not matched_langs:
#             tech_score -= 0.40
#             cons.append(
#                 f"Primary language mismatch: requires {', '.join(sheet.primary_backend_languages)}."
#             )
#         else:
#             pros.append(f"Direct match on primary language(s): {', '.join(matched_langs)}.")
#             unmatched_langs = [lang for lang in req_langs if lang not in matched_langs]
#             if unmatched_langs:
#                 tech_score -= min(0.20, 0.10 * len(unmatched_langs))
#                 cons.append(f"Secondary language gap: {', '.join(unmatched_langs)}.")
#     else:
#         tech_score -= 0.10
#         warnings.append("No explicit primary backend language identified in posting.")
#
#     # 2.3 Secondary Tools & Infrastructure Alignment
#     req_tools = [t.strip().lower() for t in sheet.secondary_tools if t.strip()]
#     matched_tools = [t for t in req_tools if any(c in t for c in [])]
#     unmatched_tools = [t for t in req_tools if not any(c in t for c in [])]
#
#     if matched_tools:
#         pros.append(f"Tooling overlap: {', '.join(matched_tools[:5])}.")
#     if unmatched_tools:
#         tool_deduction = min(0.20, 0.05 * len(unmatched_tools))
#         tech_score -= tool_deduction
#         cons.append(f"Tooling/Cloud gaps: {', '.join(unmatched_tools[:4])}.")
#
#     if not req_langs and not req_tools:
#         return MatchedJob(
#             suitability_tier=SuitabilityTier.REJECTED,
#             rejection_reason="Out-of-scope domain: No backend languages or infrastructure tools detected.",
#             confidence_score=0.95,
#             analytics=Analytics(
#                 warnings=["Non-technical/Sales/Management role detected (False Positive)."]
#             ),
#         )
#
#     # -------------------------------------------------------------------------
#     # Step 3: Strategic Value Score Evaluation (Base: 1.0)
#     # -------------------------------------------------------------------------
#     strategic_score = 1.0
#
#     if sheet.workplace_type == "REMOTE":
#         pros.append("100% remote work arrangement.")
#     elif sheet.workplace_type == "HYBRID":
#         pros.append("Hybrid role with office located in Kyiv.")
#
#     if sheet.has_uncompensated_oncall:
#         strategic_score -= 0.15
#         warnings.append("On-call rotation required without explicit compensation parameters.")
#
#     for cue in sheet.detected_operational_cues:
#         warnings.append(f"Operational risk cue: '{cue}'.")
#
#     # -------------------------------------------------------------------------
#     # Step 4: Normalization & Tier Classification
#     # -------------------------------------------------------------------------
#     tech_score = max(0.0, min(1.0, round(tech_score, 2)))
#     strategic_score = max(0.0, min(1.0, round(strategic_score, 2)))
#
#     confidence = 0.95
#     if sheet.geographic_scope == "UNKNOWN":
#         confidence -= 0.10
#     if sheet.workplace_type == "UNKNOWN":
#         confidence -= 0.10
#     if sheet.min_years_experience is None:
#         confidence -= 0.05
#     confidence = max(0.50, round(confidence, 2))
#
#     if tech_score >= 0.60 and strategic_score >= 0.60:
#         tier = SuitabilityTier.SUITABLE
#         strategic_reason = "High alignment with core technical stack and work arrangement."
#         rejection_reason = ""
#     elif tech_score < 0.60 and strategic_score >= 0.60:
#         tier = SuitabilityTier.STRETCH
#         strategic_reason = (
#             "High strategic value role with addressable technical or seniority stretch."
#         )
#         rejection_reason = ""
#     elif tech_score >= 0.50 and strategic_score < 0.60:
#         tier = SuitabilityTier.RUNWAY
#         strategic_reason = (
#             "Viable technical baseline, but lower architectural or operational alignment."
#         )
#         rejection_reason = ""
#     else:
#         tier = SuitabilityTier.REJECTED
#         strategic_reason = ""
#         rejection_reason = (
#             "Combined technical capability and strategic score fell below viable thresholds."
#         )
#
#     return MatchedJob(
#         technical_capability_score=tech_score,
#         strategic_value_score=strategic_score,
#         confidence_score=confidence,
#         suitability_tier=tier,
#         strategic_reason=strategic_reason,
#         rejection_reason=rejection_reason,
#         analytics=Analytics(pros=pros, cons=cons, warnings=warnings),
#     )
