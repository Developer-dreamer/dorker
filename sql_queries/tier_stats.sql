SELECT m.suitability_tier AS tier_type, COUNT(m.suitability_tier)
FROM matches m
WHERE m.model = 'jev-1.13.0'
  AND m.iteration = (
      SELECT MAX(mv.iteration)
      FROM matches mv
      WHERE mv.model = 'jev-1.13.0'
  )
GROUP BY m.suitability_tier;