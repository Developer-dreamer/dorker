SELECT j.id,
       j.title,
       j.description
FROM jobs j
WHERE
  -- 1. Anti-Joins for version control
  NOT EXISTS (
      SELECT 1 FROM jobs_fact_sheets jfs
      WHERE jfs.job_id = j.id AND ltrim(jfs.version, 'v')::semver >= '0.2.2'::semver
  )
  AND NOT EXISTS (
      SELECT 1 FROM matches m
      WHERE m.job_id = j.id AND ltrim(m.version, 'v')::semver >= '0.2.2'::semver
  )

  -- 2. MUST CONTAIN ONE OF THESE (Positive Match)
  AND j.searchable @@ websearch_to_tsquery('simple',
      'lang_golang OR python OR lang_csharp OR framework_dotnet OR lang_cpp OR backend OR "software engineer" OR "software developer"'
  )

  -- 3. MUST NOT CONTAIN ANY OF THESE (Negative Match)
  AND NOT j.searchable @@ websearch_to_tsquery('simple',
      'lead OR principal OR staff OR director OR architect OR manager OR vp OR head OR executive OR frontend OR "front end" OR ui OR ios OR android OR flutter OR "react native" OR php OR wordpress OR magento OR "ruby on rails" OR "network engineer" OR angular OR qa'
  )

  -- 4. Explicitly filter Title seniority to be extra safe (optional but recommended)
  AND j.title !~* '\m(lead|principal|staff|director|architect|manager|vp|head|executive|qa)\M'

  -- 5. Match Target Stack Extensions
  AND (
      j.searchable @@ to_tsquery('simple', 'python | lang_csharp | framework_dotnet | lang_cpp | lang_golang')
      OR j.title ~ '\mGo\M'
      OR j.description ~ '\mGo\M(\s*(1\.[0-9]+|developer|engineer|backend|microservices|concurrency|routine|routines|channel|channels|stack|code|programming|,|/|\band\b|\bor\b))'
      OR j.description ~ '(?i)\b(experience with|knowledge of|proficien\w+ in|proficient with|hands-on with|strong)\s+Go\b'
  )

  -- 6. Safe Location Matching
  AND (
      j.location ILIKE ANY (ARRAY['%Ukraine%', '%Europe%', '%Remote%', '%EMEA%', '%Worldwide%', '%Global%', '%віддалено%', '%Київ%'])
      OR j.location ~* '\mKy(iv|ev)\M'
  )

  AND COALESCE(j.posted_at, j.fetched_at) >= NOW() - INTERVAL '1 week'
  AND j.is_normalized = TRUE
  AND j.deleted_at IS NULL
  AND (j.description IS NOT NULL AND TRIM(j.description) != '')
ORDER BY
    ts_rank_cd(j.searchable, websearch_to_tsquery('simple', 'lang_golang OR python OR lang_csharp OR framework_dotnet OR lang_cpp OR backend OR "software engineer" OR "software developer"')) DESC,
    COALESCE(j.posted_at, j.fetched_at) DESC
LIMIT 10;
