from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Literal, Optional
from uuid import UUID

import uuid6
from pydantic import BaseModel, ConfigDict, Field, field_validator

# === Tracking entities ===


@dataclass(frozen=True)
class RuntimeVersion:
    model: str
    version: str
    iteration: int


class JobFamily(str, Enum):
    PURE_BACKEND = "PURE_BACKEND"
    FRONTEND = "FRONTEND"
    FULLSTACK = "FULLSTACK"
    QA_SDET = "QA_SDET"
    DEVOPS_PLATFORM = "DEVOPS_PLATFORM"
    MOBILE = "MOBILE"
    NON_TECHNICAL = "NON_TECHNICAL"
    OTHER = "OTHER"
    AI_ENGINEERING = "AI_ENGINEERING"
    DATA_SCIENCE = "DATA_SCIENCE"
    DATA_ENGINEERING = "DATA_ENGINEERING"
    DATA_ANALYTICS = "DATA_ANALYTICS"


class GeographicScope(str, Enum):
    UNKNOWN = "UNKNOWN"
    DOMESTIC = "DOMESTIC"
    REGIONAL = "REGIONAL"
    GLOBAL = "GLOBAL"


class WorkplaceType(str, Enum):
    REMOTE = "REMOTE"
    HYBRID = "HYBRID"
    ON_SITE = "ON_SITE"
    UNKNOWN = "UNKNOWN"


class Region(str, Enum):
    EMEA = "EMEA"
    LATAM = "LATAM"
    APAC = "APAC"
    AMER = "AMER"
    APJ = "APJ"
    CEE = "CEE"
    MENA = "MENA"
    SEA = "SEA"
    UNKNOWN = "UNKNOWN"


# === Domain models ===


class JobForAnalytics(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str

    title: str
    location: str | None

    description: str

    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None

    description_blocks: Optional[list[tuple[list[Any] | Any, float, Any]]] = Field(
        default=None, description="List of blocks with label classified, probability and exact text"
    )


# === LLM intermediate models ===


class LocationEntities(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    workplace_type: WorkplaceType = Field(
        default=WorkplaceType.UNKNOWN,
        description="Operational workplace model: REMOTE, HYBRID, or ON_SITE.",
    )
    office_location_city: Optional[str] = Field(
        default=None,
        description="Target office location/city if workplace_type is HYBRID or ON_SITE.",
    )
    geographic_scope: GeographicScope = Field(
        default=GeographicScope.UNKNOWN,
        description=(
            "Geographic classification conditioned on target_jurisdiction and region. "
            "DOMESTIC if restricted to specific countries (US only, EU only), tax forms, or clearance. "
            "GLOBAL if open worldwide with no country/bloc mandate. "
            "REGIONAL if bound to operational timezones (EMEA, LATAM, APAC)."
        ),
    )
    region: Region = Field(
        default=Region.UNKNOWN,
        description=(
            "Regional abbreviation of operational timezone requirement: EMEA, APAC, LATAM, etc. "
            "Must be null if not an operational timezone corridor."
        ),
    )

    target_jurisdiction: Optional[str] = Field(
        default=None,
        description=(
            "MUST FOLLOW 2-letter ISO 3166-1 Alpha-2 code FORMAT."
            "Country ISO code if DOMESTIC and clear country specified: US, UA, GB. Region code if "
            "legal region specified (e.g., EU). Set to null if not restricted to a single country/EU."
        ),
    )

    should_apply: Literal["Apply", "Ignore"] = Field(
        default="Apply",
        description="Flag local SLM produces, to decide whether proceed with job or not",
    )


class DomainEntities(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

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
    job_family: JobFamily = Field(
        default=JobFamily.OTHER,
        description=(
            "Final classification of role alignment. Must be consistent with the "
            "extracted primary_backend_languages, secondary_tools, and responsibilities above."
        ),
    )

    should_apply: Literal["Apply", "Ignore"] = Field(
        default="Apply",
        description="Flag local SLM produces, to decide whether proceed with job or not",
    )


class RedFlagsEntities(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

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
    # detected_operational_cues: list[str] = Field(
    #     default_factory=list,
    #     description="Exact linguistic cues indicating management debt (e.g., 'fast-paced environment', 'firefighting').",
    # )

    # =========================================================================
    # PHASE 3: Location & Jurisdiction Details (Extractive tokens)
    # =========================================================================
    workplace_type: WorkplaceType = Field(
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
            "MUST FOLLOW 2-letter ISO 3166-1 Alpha-2 code FORMAT."
            "Country ISO code if DOMESTIC and clear country specified: US, UA, GB. Region code if "
            "legal region specified (e.g., EU). Set to null if not restricted to a single country/EU."
        ),
    )
    region: Region = Field(
        default=Region.UNKNOWN,
        description=(
            "Regional abbreviation of operational timezone requirement: EMEA, APAC, LATAM, etc. "
            "Must be null if not an operational timezone corridor."
        ),
    )

    # =========================================================================
    # PHASE 4: High-Level Classification Enums (Synthesis)
    # =========================================================================
    geographic_scope: GeographicScope = Field(
        ...,
        description=(
            "Geographic classification conditioned on target_jurisdiction and region. "
            "DOMESTIC if restricted to specific countries (US only, EU only), tax forms, or clearance. "
            "GLOBAL if open worldwide with no country/bloc mandate. "
            "REGIONAL if bound to operational timezones (EMEA, LATAM, APAC)."
        ),
    )
    job_family: JobFamily = Field(
        ...,
        description=(
            "Final classification of role alignment. Must be consistent with the "
            "extracted primary_backend_languages, secondary_tools, and responsibilities above."
        ),
    )

    debug: Dict[str, str] = Field(
        default_factory=dict, description="Field used to track internal thinking of the model."
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

    @staticmethod
    def from_llm_responses(
        j_id: str,
        location: LocationEntities,
        domain: DomainEntities,
        red_flags: RedFlagsEntities,
    ) -> "JobFactSheet":
        return JobFactSheet(
            job_id=j_id,
            workplace_type=location.workplace_type,
            office_location_city=location.office_location_city,
            geographic_scope=location.geographic_scope,
            region=location.region,
            target_jurisdiction=location.target_jurisdiction,
            primary_backend_languages=domain.primary_backend_languages,
            secondary_tools=domain.secondary_tools,
            min_years_experience=domain.min_years_experience,
            is_experience_flexible=domain.is_experience_flexible,
            job_family=domain.job_family,
            is_legacy_maintenance=red_flags.is_legacy_maintenance,
            is_pure_network_or_systems=red_flags.is_pure_network_or_systems,
            has_mandatory_travel=red_flags.has_mandatory_travel,
            has_uncompensated_oncall=red_flags.has_uncompensated_oncall,
        )


class SuitabilityTier(str, Enum):
    SUITABLE = "SUITABLE"
    STRETCH = "STRETCH"
    RUNWAY = "RUNWAY"
    REJECTED = "REJECTED"


class MatchedJob(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID = Field(default_factory=uuid6.uuid7)
    job_id: str = Field(description="Job associated with this match.")

    technical_capability_score: float = Field(
        default=0.0,
        description="Value representing how good candidate's stack aligns with role's required.",
    )
    strategic_value_score: float = Field(
        default=0.0,
        description="Value representing how good job description aligns with candidate's search preferences.",
    )
    suitability_tier: SuitabilityTier = Field(
        default=SuitabilityTier.SUITABLE,
        description="""Computed directly from technical_capability_score and strategic_value_score and constraints α and β respectively.
                    - SUITABLE: technical_capability_score >= α AND strategic_value_score >= β
                    - STRETCH: technical_capability_score < α AND strategic_value_score >= β
                    - RUNWAY: technical_capability_score >= α AND strategic_value_score < β
                    - REJECTED: technical_capability_score < α AND strategic_value_score < β OR if failed other constraints like location.
                    """,
    )

    strategic_reason: str = Field(
        default="", description="Reason why a candidate should apply. Omitted when REJECTED."
    )
    rejection_reason: str = Field(
        default="", description="Reason why a job was rejected. Omitted when NOT REJECTED."
    )

    debug: str | None = Field(
        default=None, description="JSON representing raw model output or internal chain of thought."
    )
