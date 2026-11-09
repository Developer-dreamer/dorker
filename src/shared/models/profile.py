"""Reusable application facts. No model inference or dossier text is stored here."""

import re
from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uuid6
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

# ISO 3166-1 alpha-2; country groups such as EU are deliberately excluded.
COUNTRY_CODES = frozenset(
    """AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW""".split()
)
EmploymentType = Literal[
    "full_time", "part_time", "contract", "freelance", "internship", "temporary"
]
WorkArrangement = Literal["remote", "hybrid", "on_site"]
Availability = Literal["immediately", "from_date"]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Month = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{3}-(0[1-9]|1[0-2])$")]
Country = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]


def valid_country(value: str | None) -> str | None:
    if value is not None and value not in COUNTRY_CODES:
        raise ValueError("Use an ISO country code, for example UA or GB, not a country group.")
    return value


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Entry(ProfileModel):
    id: UUID = Field(default_factory=uuid6.uuid7)


class CareerEntry(Entry):
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    start_month: Month | None = None
    end_month: Month | None = None

    @model_validator(mode="after")
    def dates_and_links(self) -> Self:
        if self.start_month and self.end_month and self.end_month < self.start_month:
            raise ValueError("End month must not precede start month.")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Evidence IDs must be unique.")
        return self


class EmploymentEntry(CareerEntry):
    company: Text | None = None
    title: Text | None = None
    location: Text | None = None
    employment_type: EmploymentType | None = None
    is_current: bool | None = None

    @model_validator(mode="after")
    def current_dates(self) -> Self:
        if self.is_current is True and self.end_month is not None:
            raise ValueError("A current role cannot have an end month.")
        return self


class EducationEntry(CareerEntry):
    institution: Text | None = None
    qualification: Text | None = None
    field_of_study: Text | None = None


class LanguageEntry(Entry):
    language: Text | None = None
    proficiency: Literal["basic", "intermediate", "advanced", "fluent", "native"] | None = None


class WorkAuthorization(Entry):
    country: Country
    authorized_as_employee: bool | None = None
    sponsorship_needed_now: bool | None = None
    sponsorship_needed_later: bool | None = None
    available_as_contractor: bool | None = None

    _country = field_validator("country")(valid_country)


class CompensationExpectation(Entry):
    employment_type: EmploymentType | None = None
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    minimum: Annotated[float, Field(ge=0)] | None = None
    maximum: Annotated[float, Field(ge=0)] | None = None
    period: Literal["hourly", "monthly", "annual"]
    basis: Literal["gross", "net"]

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Minimum compensation must not exceed maximum.")
        return self


class ResumeEntry(Entry):
    label: Text
    url: HttpUrl


class ProfileDetails(ProfileModel):
    name: Text | None = None
    profile_links: list[HttpUrl] = Field(default_factory=list, max_length=100)
    first_name: Text | None = None
    last_name: Text | None = None
    preferred_name: Text | None = None
    email: Text | None = None
    phone: Text | None = None
    residence_country: Country | None = None
    residence_region: Text | None = None
    residence_city: Text | None = None
    timezone: Text | None = None
    linkedin_url: HttpUrl | None = None
    github_url: HttpUrl | None = None
    portfolio_url: HttpUrl | None = None
    desired_role_titles: list[Text] = Field(default_factory=list, max_length=100)
    employment_types: list[EmploymentType] = Field(default_factory=list)
    work_arrangements: list[WorkArrangement] = Field(default_factory=list)
    preferred_work_locations: list[Text] = Field(default_factory=list, max_length=100)
    willing_to_relocate: bool | None = None
    relocation_destinations: list[Text] = Field(default_factory=list, max_length=100)
    willing_to_travel: bool | None = None
    notice_period_days: Annotated[int, Field(ge=0)] | None = None
    availability: Availability | None = None
    earliest_start_date: date | None = None
    professional_headline: Text | None = None
    professional_summary: Annotated[str, StringConstraints(max_length=10000)] | None = None
    skills: list[Text] = Field(default_factory=list, max_length=1000)
    employment_history: list[EmploymentEntry] = Field(default_factory=list, max_length=200)
    education: list[EducationEntry] = Field(default_factory=list, max_length=200)
    languages: list[LanguageEntry] = Field(default_factory=list, max_length=100)
    work_authorizations: list[WorkAuthorization] = Field(default_factory=list, max_length=250)
    compensation_expectations: list[CompensationExpectation] = Field(
        default_factory=list, max_length=100
    )
    resumes: list[ResumeEntry] = Field(default_factory=list, max_length=100)
    default_resume_id: UUID | None = None

    _country = field_validator("residence_country")(valid_country)

    @field_validator("email")
    @classmethod
    def email_format(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("phone")
    @classmethod
    def phone_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = re.sub(r"[\s()\-]", "", value)
        if not re.fullmatch(r"\+[1-9][0-9]{6,14}", normalized):
            raise ValueError("Include an international country code, for example +380123456789.")
        return normalized

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ValueError("Enter an IANA timezone, for example Europe/Kyiv.") from error
        return value


class CareerCollection(StrEnum):
    EMPLOYMENT = "employment_history"
    EDUCATION = "education"


class CareerRecordReference(ProfileModel):
    collection: CareerCollection
    entry_id: UUID


class EvidenceLinks(ProfileModel):
    evidence_ids: list[UUID] = Field(max_length=1000)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Evidence IDs must be unique.")
        return self


class ProfileUpdate(ProfileDetails):
    """Typed partial update: model_fields_set distinguishes omission from explicit null.

    Supplied lists replace entire lists. Their elements remain domain objects,
    including server-generated IDs; this command is never a JSON/storage payload.
    """

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Supply at least one profile field.")
        if "availability" in self.model_fields_set and self.availability != "from_date":
            if self.earliest_start_date is not None:
                raise ValueError("A start date requires 'from_date' availability.")
            # Changing to immediate/unknown availability also clears any saved date.
            self.earliest_start_date = None
        return self


class ProfileOptions(ProfileModel):
    countries: list[str]
    timezones: list[str]


class UserProfile(ProfileDetails):
    id: int
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def references(self) -> Self:
        if self.availability == "from_date":
            if self.earliest_start_date is None:
                raise ValueError("Choose an earliest start date for 'from_date' availability.")
        elif self.earliest_start_date is not None:
            raise ValueError("A start date requires 'from_date' availability.")
        record_groups: tuple[tuple[str, Sequence[Entry]], ...] = (
            ("employment_history", self.employment_history),
            ("education", self.education),
            ("languages", self.languages),
            ("work_authorizations", self.work_authorizations),
            ("compensation_expectations", self.compensation_expectations),
            ("resumes", self.resumes),
        )
        for name, entries in record_groups:
            if len({entry.id for entry in entries}) != len(entries):
                raise ValueError(f"Duplicate IDs in {name}.")
        countries = [entry.country for entry in self.work_authorizations]
        if len(set(countries)) != len(countries):
            raise ValueError("Work authorization may contain only one entry per country.")
        if self.default_resume_id is not None and self.default_resume_id not in {
            r.id for r in self.resumes
        }:
            raise ValueError("Default resume must reference a saved resume entry.")
        return self

    def career_entry(self, target: CareerRecordReference) -> CareerEntry:
        entries: Sequence[CareerEntry] = (
            self.employment_history
            if target.collection == CareerCollection.EMPLOYMENT
            else self.education
        )
        for entry in entries:
            if entry.id == target.entry_id:
                return entry
        raise LookupError("Career record not found. Save the record before attaching evidence.")

    def evidence_link_update(
        self, target: CareerRecordReference, links: EvidenceLinks
    ) -> ProfileUpdate:
        """Produce a typed update while preserving the saved record and its identity."""
        self.career_entry(target)  # Reject a missing record before producing any changes.
        if target.collection == CareerCollection.EMPLOYMENT:
            jobs = [entry.model_copy(deep=True) for entry in self.employment_history]
            for job in jobs:
                if job.id == target.entry_id:
                    job.evidence_ids = list(links.evidence_ids)
            return ProfileUpdate(employment_history=jobs)
        schools = [entry.model_copy(deep=True) for entry in self.education]
        for school in schools:
            if school.id == target.entry_id:
                school.evidence_ids = list(links.evidence_ids)
        return ProfileUpdate(education=schools)
