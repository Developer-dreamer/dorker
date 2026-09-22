from logging import Logger
from pathlib import Path
from typing import Dict, Literal, Protocol, Tuple, cast, get_args

import guidance
from guidance import gen, select
from guidance.models import LlamaCpp

from .models import (
    DomainEntities,
    JobForAnalytics,
    LocationEntities,
    RedFlagsEntities,
)
from .utils import log_guidance_step

# Matches: one item without commas/newlines, followed by comma + next item
# Stops immediately when hitting a newline
open_csv_regex = r"[^,\n]+(, [^,\n]+)*"


class SLM(Protocol):
    def load_model(self) -> None: ...
    def generate[I, O](self, inp: I, out: type[O]) -> Tuple[O, Dict[str, str]]: ...


class SLMQwenThinking:
    def __init__(
        self,
        logger: Logger,
        model_path: Path | None = None,
        llm: LlamaCpp | None = None,
        thinking_token_limit: int = 150,
    ):
        self.logger = logger

        self.model_path = model_path
        self.llm = llm
        self.thinking_token_limit = thinking_token_limit

    def load_model(self) -> None:
        if self.llm is not None:
            self.logger.info(f"[{id(self)}] Already loaded. Skipping...")
            return

        self.logger.info(f"[{id(self)}] Loading model...")
        self.llm = LlamaCpp(
            model=self.model_path,
            n_gpu_layers=-1,
            n_ctx=8192,
        )
        self.logger.info(f"[{id(self)}] Model loaded.")

    def generate[I, O](self, inp: I, out: type[O]) -> Tuple[O, Dict[str, str]]:
        if self.llm is None:
            self.load_model()

        assert isinstance(inp, JobForAnalytics), f"Expected JobForAnalytics, got {type(inp)}"

        match out:
            case t if t is LocationEntities:
                return cast(Tuple[O, Dict[str, str]], self._retrieve_location(inp))
            case t if t is DomainEntities:
                return cast(Tuple[O, Dict[str, str]], self._retrieve_domain_entities(inp))
            case t if t is RedFlagsEntities:
                return cast(Tuple[O, Dict[str, str]], self._retrieve_hidden_redflags(inp))
            case _:
                raise TypeError(f"Unsupported output type requested: {out}")

    def _retrieve_location(self, job: JobForAnalytics) -> Tuple[LocationEntities, Dict[str, str]]:
        assert self.llm is not None
        if not job.description_blocks:
            self.logger.warning("Unable to process job. No description blocks.")
            return LocationEntities(), {}

        llm = self.llm
        debug: Dict[str, str] = dict()

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
                llm += (
                    "<think>\n"
                    + gen(
                        name="ev_workplace",
                        max_tokens=self.thinking_token_limit,
                        temperature=0.5,
                    )
                    + "\n</think>\n"
                )
                debug["ev_workplace"] = llm["ev_workplace"]

                options = list(get_args(LocationEntities.model_fields["workplace_type"].annotation))
                llm += "Selected workplace: " + select(options, name="workplace_type") + "\n\n"
                self.logger.debug(
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
                        "<think>\n"
                        + gen(
                            name="ev_office_city",
                            max_tokens=self.thinking_token_limit,
                            temperature=0.5,
                        )
                        + "\n</think>\n"
                    )
                    debug["ev_office_city"] = llm["ev_office_city"]
                    llm += (
                        "Selected office location: " + gen(name="office_location", stop="\n") + "\n"
                    )
                    if llm["office_location"]:
                        location_entity.office_location_city = llm["office_location"]
                    self.logger.debug(
                        f"[{job.id}] Office Ev: '{llm['ev_office_city']}' -> {location_entity.office_location_city}"
                    )
                    step += 1

                llm += f"""Step {step} - Extract jurisdiction. Identify the primary country where the candidate must be legally authorized to work or reside.
                        - Format as a 2-letter ISO 3166-1 Alpha-2 code (e.g., "US", "GB", "UA", "DE", "EU").
                        - If no specific country is required (e.g. worldwide remote), or if a broad region (EMEA/LATAM) is specified without a specific country, output "UNKNOWN".
                        """
                llm += (
                    "<think>\n"
                    + gen(
                        name="ev_jurisdiction",
                        max_tokens=self.thinking_token_limit,
                        temperature=0.5,
                    )
                    + "\n</think>\n"
                )
                debug["ev_jurisdiction"] = llm["ev_jurisdiction"]

                llm += (
                    "Selected jurisdiction: "
                    + gen(regex=r"[A-Z]{2}|UNKNOWN", name="target_jurisdiction")
                    + "\n"
                )

                if llm["target_jurisdiction"] != "UNKNOWN":
                    location_entity.target_jurisdiction = llm["target_jurisdiction"]
                self.logger.debug(
                    f"[{job.id}] Jurisdiction Ev: '{llm['ev_jurisdiction']}' -> {llm['target_jurisdiction']}"
                )
                step += 1

                llm += f"""Step {step} - Extract geographic region. Identify if the role is restricted to a specific timezone or continental corridor (e.g., EMEA, LATAM, APAC, AMERICAS).
                                        - If no broad region is specified, output "UNKNOWN".
                                        """
                llm += (
                    "<think>\n"
                    + gen(name="ev_region", max_tokens=self.thinking_token_limit, temperature=0.5)
                    + "\n</think>\n"
                )
                debug["ev_region"] = llm["ev_region"]

                region_options = list(get_args(LocationEntities.model_fields["region"].annotation))
                llm += "Selected region: " + select(region_options, name="region") + "\n\n"
                location_entity.region = llm["region"]

                self.logger.debug(f"[{job.id}] Region Ev: '{llm['ev_region']}' -> {llm['region']}")
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
                llm += (
                    "<think>\n"
                    + gen(name="ev_scope", max_tokens=self.thinking_token_limit, temperature=0.5)
                    + "\n</think>\n"
                )
                debug["ev_scope"] = llm["ev_scope"]

                options = list(
                    get_args(LocationEntities.model_fields["geographic_scope"].annotation)
                )
                llm += (
                    "Selected geographic scope: "
                    + select(options, name="geographic_scope")
                    + "\n\n"
                )
                self.logger.debug(
                    f"[{job.id}] Scope Ev: '{llm['ev_scope']}' -> {llm['geographic_scope']}"
                )
                location_entity.geographic_scope = llm["geographic_scope"]

                self.logger.info(
                    f"[{job.id}] LocationEntities: Scope={location_entity.geographic_scope} | "
                    f"Workplace={location_entity.workplace_type} | "
                    f"Region={location_entity.region} | "
                    f"Jurisdiction={location_entity.target_jurisdiction} | "
                    f"Office location={location_entity.office_location_city}"
                )

                location_entity.should_apply = self._evaluate_location_relevance(location_entity)
                self.logger.info(f"Should apply? Script decided: {location_entity.should_apply}")

            tracker["llm"] = llm

        return location_entity, debug

    @staticmethod
    def _evaluate_location_relevance(location: LocationEntities) -> Literal["Apply", "Ignore"]:
        if location.workplace_type in ["ON_SITE", "HYBRID"]:
            city = str(location.office_location_city or "").lower()
            if "kyiv" not in city and "kiev" not in city:
                return "Ignore"

        if location.geographic_scope == "DOMESTIC":
            valid_domestic_codes = ["UA", "UN"]

            if (
                location.target_jurisdiction
                and location.target_jurisdiction not in valid_domestic_codes
            ):
                return "Ignore"

        if location.geographic_scope == "REGIONAL":
            incompatible_regions = ["AMER", "LATAM", "APAC", "APJ", "SEA"]
            if location.region in incompatible_regions:
                return "Ignore"

        return "Apply"

    def _retrieve_domain_entities(
        self, job: JobForAnalytics
    ) -> Tuple[DomainEntities, Dict[str, str]]:
        assert self.llm is not None
        if not job.description_blocks:
            self.logger.warning("Unable to process job. No description blocks.")
            return DomainEntities(), {}

        llm = self.llm
        debug: Dict[str, str] = {}

        description = f"Title: {job.title}\nDescription:\n"
        for key, _, text in job.description_blocks:
            if key in ["REQUIREMENTS", "RESPONSIBILITIES"]:
                description += text

        domain_entity = DomainEntities()

        with log_guidance_step(self.logger, str(job.id), "Domain", llm) as tracker:
            with guidance.user():
                llm += f"""Analyze this job description:{description}.
                        Identify and save all entities that could be required
                        to match the job to the candidate: languages, tools,
                        years of experience, requirements, responsibilities, etc.
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
                    "<think>\n"
                    + gen(name="ev_langs", max_tokens=self.thinking_token_limit, temperature=0.5)
                    + "\n</think>\n"
                )
                debug["ev_langs"] = llm["ev_langs"]
                llm += (
                    "Primary backend languages extracted: "
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
                    "<think>\n"
                    + gen(name="ev_tools", max_tokens=self.thinking_token_limit, temperature=0.5)
                    + "\n</think>\n"
                )
                debug["ev_tools"] = llm["ev_tools"]
                llm += (
                    "Secondary tools extracted: "
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
                llm += (
                    "<think>\n"
                    + gen(
                        name="ev_yoe",
                        max_tokens=self.thinking_token_limit,
                        temperature=0.5,
                    )
                    + "\n</think>\n"
                )
                debug["ev_yoe"] = llm["ev_yoe"]

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
                llm += (
                    "<think>\n"
                    + gen(
                        name="ev_flexibility",
                        max_tokens=self.thinking_token_limit,
                        temperature=0.5,
                    )
                    + "\n</think>\n"
                )
                debug["ev_flexibility"] = llm["ev_flexibility"]
                llm += (
                    "Is experience flexible? "
                    + select(["true", "false"], name="flexibility")
                    + "\n"
                )
                domain_entity.is_experience_flexible = llm["flexibility"] == "true"
                step += 1

                llm += f"""Step {step} - Identify job family. It is defined by next constraints:
                        - PURE_BACKEND: Traditional backend (APIs, CRUD, distributed systems, DBs). No AI/ML components in production deliverables.
                        - AI_ENGINEERING: Building software/APIs powered by AI (RAG, LLM orchestration, vector DBs, model serving/inference pipelines). The engineer integrates or deploys models rather than training them.
                        - DATA_SCIENCE: Core ML/AI research and training. Training models, fine-tuning weights, predictive algorithms, statistical modeling (PyTorch, TensorFlow, scikit-learn).
                        - DATA_ENGINEERING: Data engineering, and business intelligence (ETL pipelines, Airflow, Spark, dbt, SQL, dashboards).
                        - DATA_ANALYTICS: Data analytics, business metrics, etc.
                        - FRONTEND: UI, UX, web clients (React, Vue, CSS).
                        - FULLSTACK: Balanced combination of frontend and backend.
                        - MOBILE: if role's primary focus is mobile or desktop development (React Native, Swift, Java, Kotlin, etc.) or it is any of other types, however mentiones in requirements commitment to mobile development (e.g.: "This is backend role, however you're required to maintain our Kotlin app...").
                        - DEVOPS_PLATFORM: CI/CD, cloud infrastructure, Kubernetes, Terraform, developer tooling.
                        - QA_SDET: Automated testing, QA frameworks, manual testing.
                        - NON_TECHNICAL: Sales, customer support, technical writing, recruiting.
                        - OTHER: Specialized technical roles not covered above (e.g. Embedded/Firmware, Hardware, Computational Physics).
                        """
                llm += (
                    "<think>\n"
                    + gen(name="ev_family", max_tokens=self.thinking_token_limit, temperature=0.5)
                    + "\n</think>\n"
                )
                debug["ev_family"] = llm["ev_family"]

                options = list(get_args(DomainEntities.model_fields["job_family"].annotation))
                llm += "Selected job family: " + select(options, name="job_family") + "\n"
                domain_entity.job_family = llm["job_family"]

                self.logger.info(
                    f"[{job.id}] Domain: Family={domain_entity.job_family} | "
                    f"YoE={domain_entity.min_years_experience} | "
                    f"Langs={domain_entity.primary_backend_languages}"
                )

                llm += """ Final step. Consider whether this candidate should apply to this role or not:
                    <cv>
                    ## Core CV / Resume
                    <title>Backend Engineer | AI & Cloud Infrastructure</title>
                    <summary>
                    Backend Software Engineer specializing in distributed systems, cloud infrastructure, and real-time streaming architectures. Polyglot thinker with proven experience engineering event-driven microservices using Go and Python. Expert in protocol debugging, database query optimization, and implementing baseline observability. Strong foundation in algorithms, demonstrated by instructing 220+ university students. Obtaining degree in Artificial Intelligence at Kyiv School of Economics. Pursuing career towards deep AI Infra and R&D Engineering.
                    </summary>
                    <hard_skills>
                    Score near technology is high confident I feel to use this tool between 0 and 1 (1 = use confidently).
                    - Python 0.8
                    - AI (ML 0.3, RL 0.1)
                    - NumPy/Pandas 0.5, Matplotlib 0.4, PyTorch 0.1
                    - Architecture (Microservices 0.5, Event-driven 0.5, Orchestration 0.4, Observability/OpenTelemetry 0.3)
                    - Go 0.8 (WebRTC/pion 0.3)
                    - Testing (Unit/Integration 0.7)
                    - Databases (PostgreSQL 0.8, GCP Firestore 0.6, Redis 0.5, MongoDB 0.3)
                    - Git/GitHub 0.8, GitLab 0.4
                    - DevOps/Cloud (Linux 0.8, Docker 0.8, GCP 0.6, Terraform 0.6, CI/CD 0.3)
                    - C#/.NET 0.8 (ASP.NET Core 0.5, EF Core 0.6)
                    - C/C++ 0.4
                    </hard_skills>
                    <experience>
                    ### Go WebRTC Engineer | Pixelview.io | Jan-Mar 2026
                    Integrated WHIP/Trickle ICE into Go/Pion engine; deployed TURN server via Nomad/HCL; refactored monolith, updated CGO FFmpeg transcoding pipeline. End-to-end delivery.
                    ### Go Backend Engineer | Headway Inc. | Jun 2025
                    Engineered concurrent Go analytics microservice. Multi-db storage (PostgreSQL/sqlx, Firestore). Redis caching. Automated mock generation, integration tests with Testcontainers. Structured logging, API-key auth. CLI mock event generator. Goose migrations. Terraform IaC (Cloud Run, Cloud SQL, VPCs). GitLab CI/CD pipelines.
                    ### .NET Backend Developer | EPAM Systems | May 2025
                    Layered Web API using C#/.NET 9.0, EF Core, PostgreSQL. Redis caching, Docker containerization. NUnit/Moq tests, GitHub Actions CI.
                    ### Script Developer | MacPaw Inc. | Aug-Sep 2024
                    Engineered Python/JS CLI scripts and automation tools for macOS workflows. Integrated internal/3rd-party APIs. Automated text/data processing via LLMs/prompt engineering.
                    ### Teaching Assistant | KSE | Sep 2025-Apr 2026
                    Taught Algorithms, Data Structures, and Python to 220+ students. Live-coding, code reviews, assignment design.
                    </experience>
                    <education>
                    BSc Artificial Intelligence & Software Engineering, Kyiv School of Economics. Top marks in Algorithms, Discrete Math. Coursework in Distributed Systems, Microservices, API Design, Parallel Programming. Deep Learning/NLP/CV scheduled.
                    </education>
                    <personal_projects>
                    ## AI Orchestrator
                    Distributed AI-orchestration system. Async processing via Redis Streams. OpenTelemetry/Grafana observability. Docker deployed, Terraform/GCP planned.
                    ## Dorker
                    Python app scraping 40+ job boards, filtering 2m+ jobs. Uses ML/LLM pipeline (Sentence Transformers, Logistic Regression, SLMs: deepseek-r1-distill-qwen-7b, Guidance framework). Postgres storage. Complex text data processing, pipeline orchestration.
                    </personal_projects>
                    </cv>
                    """

                llm += (
                    "<think>\n"
                    + gen(name="ev_application", max_tokens=250, temperature=0.5)
                    + "\n</think>\n"
                )
                debug["ev_application"] = llm["ev_application"]
                llm += "Should apply? " + select(["Apply", "Ignore"], name="application") + "\n"
                domain_entity.should_apply = llm["application"]

                self.logger.info(f"Should apply? Model decided to: {domain_entity.should_apply}")

            tracker["llm"] = llm

        return domain_entity, debug

    def _retrieve_hidden_redflags(
        self, job: JobForAnalytics
    ) -> Tuple[RedFlagsEntities, Dict[str, str]]:
        assert self.llm is not None
        if not job.description_blocks:
            self.logger.warning("Unable to process job. No description blocks.")
            return RedFlagsEntities(), {}

        llm = self.llm
        debug: Dict[str, str] = {}

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
                llm += f"""Analyze this job description:{description}."""

            with guidance.assistant():
                step = 1
                llm += f"""Step {step} - Identify if role requires legacy maintenance. 
                        It is defined by next constraints:
                        If role explicitly specifies "you will be supporting/maintaining our PHP monolith" or 
                        "you will be responsible for maintaining our python monolith". 
                        No new features delivery mentioned. Pure maintenance role.
                        """
                llm += (
                    "<think>\n"
                    + gen(name="ev_legacy", max_tokens=self.thinking_token_limit, temperature=0.5)
                    + "\n</think>\n"
                )
                debug["ev_legacy"] = llm["ev_legacy"]

                llm += (
                    "Is legacy maintenance required? "
                    + select(["true", "false"], name="is_legacy")
                    + "\n"
                )
                red_flags.is_legacy_maintenance = llm["is_legacy"] == "true"
                step += 1

                llm += f"""Step {step} - Identify if role is pure networks or systems. 
                            It is defined by next constraints:
                            exists to differentiate backend developer engineering roles with core engineering roles. 
                            If role specifies that most of you're daily work will consist of network exploration, 
                            protocol building/debugging, without shipping features, 
                            focusing only on basic network/system engineering.
                            """
                llm += (
                    "<think>\n"
                    + gen(name="ev_network", max_tokens=self.thinking_token_limit, temperature=0.5)
                    + "\n</think>\n"
                )
                debug["ev_network"] = llm["ev_network"]

                llm += (
                    "Is pure network or systems role? "
                    + select(["true", "false"], name="is_network")
                    + "\n"
                )
                red_flags.is_pure_network_or_systems = llm["is_network"] == "true"
                step += 1

                llm += f"""Step {step} - Identify if role requires travel. 
                                        Check if role explicitly states that mandatory travel required N-times per year/quartal/etc.
                                        """
                llm += (
                    "<think>\n"
                    + gen(name="ev_travel", max_tokens=self.thinking_token_limit, temperature=0.5)
                    + "\n</think>\n"
                )
                debug["ev_travel"] = llm["ev_travel"]

                llm += "Does require travel? " + select(["true", "false"], name="is_travel") + "\n"
                red_flags.has_mandatory_travel = llm["is_travel"] == "true"
                step += 1

                llm += f"""Step {step} - Identify if role has oncall rotation. 
                        Check if role explicitly requires on call rotation.
                        """
                llm += (
                    "<think>\n"
                    + gen(name="ev_oncall", max_tokens=self.thinking_token_limit, temperature=0.5)
                    + "\n</think>\n"
                )
                debug["ev_oncall"] = llm["ev_oncall"]

                llm += "Does require oncall? " + select(["true", "false"], name="has_oncall") + "\n"
                red_flags.has_uncompensated_oncall = llm["has_oncall"] == "true"

                self.logger.info(
                    f"[{job.id}] RedFlags: Legacy={red_flags.is_legacy_maintenance} | "
                    f"NetSys={red_flags.is_pure_network_or_systems} | "
                    f"Travel={red_flags.has_mandatory_travel} | "
                    f"OnCall={red_flags.has_uncompensated_oncall}"
                )

            tracker["llm"] = llm

        return red_flags, debug
