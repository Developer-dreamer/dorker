WITH RankedMatches AS (SELECT m.*,
                              ROW_NUMBER() OVER (
                                  PARTITION BY m.job_id
                                  ORDER BY m.iteration DESC, m.created_at DESC
                                  ) as rn
                       FROM matches m
                       WHERE m.pipeline_status = 'PENDING'
                         -- Optionally filter by specific model/version if you are testing variations:
                         AND m.model = 'jev-1.13.0'
                         AND ltrim(m.version, 'v')::semver >= '0.2.2'::semver)
SELECT rm.suitability_tier, COUNT(*)

FROM RankedMatches rm
         JOIN jobs j ON j.id = rm.job_id
WHERE rn = 1
  AND j.deleted_at IS NULL
GROUP BY suitability_tier;

