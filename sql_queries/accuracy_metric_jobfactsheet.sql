WITH comparisons AS (
    SELECT
        (g.workplace_type IS NOT DISTINCT FROM m.workplace_type)::int AS workplace_type,
        (g.job_family IS NOT DISTINCT FROM m.job_family)::int AS job_family,
        (g.min_years_experience IS NOT DISTINCT FROM m.min_years_experience)::int AS min_years_experience,
        (g.office_location_city IS NOT DISTINCT FROM m.office_location_city)::int AS office_location_city,
        (g.is_experience_flexible IS NOT DISTINCT FROM m.is_experience_flexible)::int AS is_experience_flexible,
        (g.is_legacy_maintenance IS NOT DISTINCT FROM m.is_legacy_maintenance)::int AS is_legacy_maintenance,
        (g.is_pure_network_or_systems IS NOT DISTINCT FROM m.is_pure_network_or_systems)::int AS is_pure_network_or_systems,
        (g.has_mandatory_travel IS NOT DISTINCT FROM m.has_mandatory_travel)::int AS has_mandatory_travel,
        (g.has_uncompensated_oncall IS NOT DISTINCT FROM m.has_uncompensated_oncall)::int AS has_uncompensated_oncall,
        (g.geographic_scope IS NOT DISTINCT FROM m.geographic_scope)::int AS geographic_scope,
        (g.region IS NOT DISTINCT FROM m.region)::int AS region,
        (g.timezone_overlap_requested IS NOT DISTINCT FROM m.timezone_overlap_requested)::int AS timezone_overlap_requested,
        (g.target_jurisdiction IS NOT DISTINCT FROM m.target_jurisdiction)::int AS target_jurisdiction,
        (CASE
             WHEN (g.primary_backend_languages IS NULL OR cardinality(g.primary_backend_languages) = 0)
                 AND (m.primary_backend_languages IS NULL OR cardinality(m.primary_backend_languages) = 0) THEN 1.0
             WHEN g.primary_backend_languages IS NULL OR m.primary_backend_languages IS NULL THEN 0.0
             ELSE
                 COALESCE(
                         cardinality(ARRAY(SELECT unnest(g.primary_backend_languages) INTERSECT SELECT unnest(m.primary_backend_languages)))::numeric
                     / NULLIF(cardinality(ARRAY(SELECT unnest(g.primary_backend_languages) UNION SELECT unnest(m.primary_backend_languages))), 0),
                         0.0
                 )
            END) AS primary_backend_languages,
        (CASE
             WHEN (g.secondary_tools IS NULL OR cardinality(g.secondary_tools) = 0)
                 AND (m.secondary_tools IS NULL OR cardinality(m.secondary_tools) = 0) THEN 1.0
             WHEN g.secondary_tools IS NULL OR m.secondary_tools IS NULL THEN 0.0
             ELSE
                 COALESCE(
                         cardinality(ARRAY(SELECT unnest(g.secondary_tools) INTERSECT SELECT unnest(m.secondary_tools)))::numeric
                     / NULLIF(cardinality(ARRAY(SELECT unnest(g.secondary_tools) UNION SELECT unnest(m.secondary_tools))), 0),
                         0.0
                 )
            END) AS secondary_tools
    FROM jobs_fact_sheets g
             INNER JOIN jobs_fact_sheets m
                        ON g.job_id = m.job_id
                            AND m.version = 'v0.2.0'
    WHERE g.model = 'golden_set_manual'
)
SELECT
    field_name,
    ROUND(AVG(is_match) * 100, 2) AS accuracy_percentage
FROM comparisons
         CROSS JOIN LATERAL (
    VALUES
        ('workplace_type', workplace_type),
        ('job_family', job_family),
        ('min_years_experience', min_years_experience),
        ('office_location_city', office_location_city),
        ('is_experience_flexible', is_experience_flexible),
        ('is_legacy_maintenance', is_legacy_maintenance),
        ('is_pure_network_or_systems', is_pure_network_or_systems),
        ('has_mandatory_travel', has_mandatory_travel),
        ('has_uncompensated_oncall', has_uncompensated_oncall),
        ('geographic_scope', geographic_scope),
        ('region', region),
        ('timezone_overlap_requested', timezone_overlap_requested),
        ('target_jurisdiction', target_jurisdiction),
        ('primary_backend_languages', primary_backend_languages),
        ('secondary_tools', secondary_tools)
        ) AS unpivoted(field_name, is_match)
GROUP BY field_name
ORDER BY accuracy_percentage DESC;