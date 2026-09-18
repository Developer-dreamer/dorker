SELECT j.id,
       j.title,
       j.description
FROM jobs j
         CROSS JOIN websearch_to_tsquery('simple',
                                         '(lang_golang OR python OR lang_csharp OR framework_dotnet OR lang_cpp OR backend OR software engineer OR software developer) '
                                             '-lead -principal -staff -director -architect -manager -vp -head -executive '
                                             '-frontend -front end -ui -ios -android -flutter -react native -php -wordpress -magento -ruby on rails -network engineer -angular'
                    ) AS query
WHERE j.searchable @@ query
  AND (
-- 1. Unambiguous non-Go matches via normalized FTS
    j.searchable @@ to_tsquery('simple', 'python | lang_csharp | framework_dotnet | lang_cpp | lang_golang')

-- 2. Case-sensitive 'Go' in Title
   OR j.title ~ '\mGo\M'

-- 3. Contextual Go in Description
   OR j.description ~
    '\mGo\M(\s*(1\.[0-9]+|developer|engineer|backend|microservices|concurrency|routine|routines|channel|channels|stack|code|programming|,|/|\band\b|\bor\b))'
   OR
    j.description ~ '(?i)\b(experience with|knowledge of|proficien\w+ in|proficient with|hands-on with|strong)\s+Go\b'
    )
  AND NOT j.title ~ '\mQA\M'
  AND j.location ILIKE ANY
    (ARRAY ['%Ukraine%', '%Europe%', '%Remote%', '%EMEA%', '%Worldwide%', '%Global%', '%віддалено%', '%/Ky(iv|ev)/i%', '%Київ%'])
  AND COALESCE(posted_at, fetched_at) >= NOW() - INTERVAL '7 days'
  AND is_normalized = TRUE
  AND deleted_at IS NULL
ORDER BY ts_rank_cd(j.searchable, query) DESC,
    COALESCE(j.posted_at, j.fetched_at) DESC;