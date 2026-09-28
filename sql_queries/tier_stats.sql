SELECT m.suitability_tier AS tier_type, COUNT(m.suitability_tier)
FROM matches m
WHERE ltrim(m.version, 'v')::semver >= '0.3.0'::semver
GROUP BY m.suitability_tier;