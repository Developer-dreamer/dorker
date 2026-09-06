from typing import Any, Dict, List

import psycopg2
import psycopg2.extras
import streamlit as st
import uuid6 as uuid
from psycopg2.extensions import connection

# Configure wide layout
st.set_page_config(layout="wide", page_title="Golden Set Labeler")

# Database configuration
DB_CONFIG = {
    "dbname": "dorker_db",
    "user": "postgres",
    "password": "password",
    "host": "localhost",
    "port": 5432,
}

PIPELINE_VER = "v0.1.3"
MODEL_TAG = "golden_set_manual"

INFRA = ['smartrecruiters:744000141710019','smartrecruiters:744000142054520','dou:361822','adp:41a778b2-3a5e-42c7-9770-87f62458fb3e:9200865371904_2:9201277181986_1','dou:368562','dou:365325','greenhouse:8749950002','dou:371742','dou:359220','dou:369470','greenhouse:4705232006','greenhouse:4705191006','greenhouse:8701253002','dou:370649','dou:369435','greenhouse:6173884004','rippling:052b68e5-9417-4be3-8e5b-fd28d45b1c86','greenhouse:8682860002','dou:369168','dou:370677']
AI = ['dou:353725','ashby:e5b56446-5cf6-4acc-85ed-2612873c5148','dou:371076','dou:352466','dou:369989','dou:368701','greenhouse:5220321007','dou:368813','jazzhr:wFjXAodd3Y','dou:370924','dou:369992','dou:370712','dou:363877','dou:371009','dou:369636','dou:371691','greenhouse:5365275008','dou:371360','greenhouse:4963900101','dou:364511']

JOB_IDS = list(set(INFRA) | set(AI))


@st.cache_resource
def get_db_connection() -> connection:
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    return conn


def load_jobs(conn: connection, ids: List[str]) -> Dict[str, Any]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, title, description, location FROM jobs WHERE id = ANY(%s)", (ids,))
        return {row["id"]: row for row in cur.fetchall()}


def save_golden_record(conn: connection, payload: Dict[str, Any]) -> None:
    query = f"""
            INSERT INTO jobs_fact_sheets (
                id, job_id, job_family, geographic_scope, workplace_type, target_jurisdiction, office_location_city, timezone_overlap_requested,
                min_years_experience, is_experience_flexible, primary_backend_languages, secondary_tools,
                is_legacy_maintenance, is_pure_network_or_systems, has_mandatory_travel,
                has_uncompensated_oncall, detected_operational_cues, model, version
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{MODEL_TAG}', '{PIPELINE_VER}'
            ) ON CONFLICT (id) DO UPDATE SET
            job_family = EXCLUDED.job_family,
            geographic_scope = EXCLUDED.geographic_scope,
            workplace_type = EXCLUDED.workplace_type,
            target_jurisdiction = EXCLUDED.target_jurisdiction,
            office_location_city = EXCLUDED.office_location_city,
            timezone_overlap_requested = EXCLUDED.timezone_overlap_requested,
            min_years_experience = EXCLUDED.min_years_experience,
            is_experience_flexible = EXCLUDED.is_experience_flexible,
            primary_backend_languages = EXCLUDED.primary_backend_languages,
            secondary_tools = EXCLUDED.secondary_tools,
            is_legacy_maintenance = EXCLUDED.is_legacy_maintenance,
            is_pure_network_or_systems = EXCLUDED.is_pure_network_or_systems,
            has_mandatory_travel = EXCLUDED.has_mandatory_travel,
            has_uncompensated_oncall = EXCLUDED.has_uncompensated_oncall,
            detected_operational_cues = EXCLUDED.detected_operational_cues,
            model = EXCLUDED.model,
            version = EXCLUDED.version;
            """

    params = (
        payload["id"],
        payload["job_id"],
        payload["job_family"],
        payload["geographic_scope"],
        payload["workplace_type"],
        payload["target_jurisdiction"] or None,
        payload["office_location_city"],
        payload["timezone_overlap_requested"],
        payload["min_years_experience"],
        payload["is_experience_flexible"],
        payload["primary_backend_languages"],
        payload["secondary_tools"],
        payload["is_legacy_maintenance"],
        payload["is_pure_network_or_systems"],
        payload["has_mandatory_travel"],
        payload["has_uncompensated_oncall"],
        payload["detected_operational_cues"],
    )

    with conn.cursor() as cur:
        cur.execute(query, params)


conn = get_db_connection()

if "job_data" not in st.session_state:
    st.session_state.job_data = load_jobs(conn, JOB_IDS)
    st.session_state.index = 0

jobs_list = [st.session_state.job_data[jid] for jid in JOB_IDS if jid in st.session_state.job_data]

if not jobs_list:
    st.error("No matching jobs found in the database.")
    st.stop()

total = len(jobs_list)
current_idx = st.session_state.index
current_job = jobs_list[current_idx]

col_left, col_right = st.columns([1.1, 0.9], gap="medium")

with col_left:
    st.subheader(f"[{current_idx + 1}/{total}] `{current_job['id']}` — {current_job['title']}")

    raw_location = current_job.get("location") or "Not Specified"
    st.caption(f"📍 **Raw DB Location:** `{raw_location}`")

    with st.container(height=750):
        st.markdown(current_job["description"])

with col_right:
    st.subheader("Fact Sheet Annotation")
    col_nav1, col_nav2 = st.columns(2)
    with col_nav1:
        if st.button("Previous") and st.session_state.index > 0:
            st.session_state.index -= 1
            st.rerun()
    with col_nav2:
        if st.button("Skip") and st.session_state.index < total - 1:
            st.session_state.index += 1
            st.rerun()

    with st.form(key=f"form_{current_job['id']}"):
        st.markdown("##### Job Family")
        job_family = st.selectbox(
            "Job Family",
            [
                "OTHER",
                "BACKEND",
                "FRONTEND",
                "FULLSTACK",
                "QA_SDET",
                "DEVOPS_PLATFORM",
                "DATA_AI",
                "MOBILE",
                "NON_TECHNICAL",
            ],
            index=0,
        )
        st.markdown("##### Workplace & Location")
        col_w1, col_w2 = st.columns(2)
        with col_w1:
            workplace_type = st.selectbox(
                "Workplace Type",
                ["REMOTE", "HYBRID", "ON_SITE", "UNKNOWN"],
                index=0,
            )
            office_location_city = st.text_input(
                "Office Location / City",
                value=current_job.get("location", "") if current_job.get("location") else "",
                placeholder="e.g., San Francisco, CA or Kyiv",
            )
            timezone_overlap_requested = st.text_input(
                "Timezone Overlap Requested",
                placeholder="e.g., EST ±3h, UTC+2",
            )
        with col_w2:
            geographic_scope = st.selectbox(
                "Geographic Scope",
                [
                    "UNKNOWN",
                    "STRICT_DOMESTIC_ONLY",
                    "GLOBAL_OR_EMEA",
                    "TIMEZONE_OVERLAP_ONLY",
                ],
                index=0,
            )
            target_jurisdiction = st.text_input(
                "Target jurisdiction",
                placeholder="e.g., US, UA",
            )

        st.markdown("##### Experience & Seniority")
        col_e1, col_e2 = st.columns([1, 1])
        with col_e1:
            min_years_experience = st.number_input(
                "Min Years Experience (0 = unspecified/none)",
                min_value=0,
                max_value=30,
                value=0,
                step=1,
            )
        with col_e2:
            st.write("")
            st.write("")
            is_experience_flexible = st.checkbox("Is Experience Flexible", value=False)

        st.markdown("##### Tech Stack")
        primary_backend_languages_raw = st.text_input(
            "Primary Backend Languages (comma-separated)",
            placeholder="Go, Python, Rust, Java",
        )
        secondary_tools_raw = st.text_input(
            "Secondary Tools (comma-separated)",
            placeholder="PostgreSQL, Docker, Kafka, Redis, Terraform",
        )

        st.markdown("##### Operational & Role Constraints")
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            is_legacy_maintenance = st.checkbox("Is Legacy Maintenance", value=False)
            is_pure_network_or_systems = st.checkbox("Is Pure Network / Systems", value=False)
        with col_c2:
            has_mandatory_travel = st.checkbox("Has Mandatory Travel", value=False)
            has_uncompensated_oncall = st.checkbox("Has Uncompensated On-call", value=False)

        detected_operational_cues_raw = st.text_area(
            "Detected Operational Cues (comma-separated)",
            placeholder="e.g., 24/7 rotation, client facing, high concurrency, strict SLA",
            height=70,
        )

        submitted = st.form_submit_button("Save Fact Sheet & Next")
        if submitted:

            def parse_csv(val: str) -> list[str]:
                cleaned = val.replace('"', '').replace("'", "")
                return [s.strip() for s in cleaned.split(",") if s.strip()]

            payload = {
                "id": str(uuid.uuid7()),
                "job_id": current_job["id"],
                "job_family": job_family,
                "geographic_scope": (
                    geographic_scope.strip() if geographic_scope.strip() else None
                ),
                "workplace_type": workplace_type,
                "target_jurisdiction": target_jurisdiction,
                "office_location_city": (
                    office_location_city.strip() if office_location_city.strip() else None
                ),
                "timezone_overlap_requested": (
                    timezone_overlap_requested.strip()
                    if timezone_overlap_requested.strip()
                    else None
                ),
                "min_years_experience": (
                    int(min_years_experience) if min_years_experience > 0 else None
                ),
                "is_experience_flexible": is_experience_flexible,
                "primary_backend_languages": parse_csv(primary_backend_languages_raw),
                "secondary_tools": parse_csv(secondary_tools_raw),
                "is_legacy_maintenance": is_legacy_maintenance,
                "is_pure_network_or_systems": is_pure_network_or_systems,
                "has_mandatory_travel": has_mandatory_travel,
                "has_uncompensated_oncall": has_uncompensated_oncall,
                "detected_operational_cues": parse_csv(detected_operational_cues_raw),
            }

            save_golden_record(conn, payload)

            if st.session_state.index < total - 1:
                st.session_state.index += 1
                st.rerun()
            else:
                st.success(f"All {len(JOB_IDS)} jobs processed.")

