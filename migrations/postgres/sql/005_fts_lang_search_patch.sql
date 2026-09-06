CREATE OR REPLACE FUNCTION normalize_tech_text(text) RETURNS text AS $$
    SELECT regexp_replace(
        regexp_replace(
            regexp_replace(
                regexp_replace($1, 
                    '(?i)\bc#\b', 'lang_csharp', 'g'),
                '(?i)\bc\+\+\b', 'lang_cpp', 'g'),
            '(?i)\b(\.net|dotnet|asp\.net)\b', 'framework_dotnet', 'g'),
        '(?i)\bgolang\b', 'lang_golang', 'g'
    );
$$ LANGUAGE SQL IMMUTABLE;