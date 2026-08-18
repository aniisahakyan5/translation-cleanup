-- =====================================================================
--  dictionary.sql  --  INPUT 2 of 2: the Dictionary.
--
--  Run this ALONGSIDE db_keys.sql. It is not optional, and db_keys.sql
--  cannot replace it: the `source` column below exists only here, and
--  every source_missing / source_mismatch / expected_source verdict is
--  measured against it. Without this file the reconciler has nothing to
--  compare actual usage to.
--
--  public.dictionary is the ONLY source of dictionary keys, which is why
--  the dictionary branch was removed from db_keys.sql -- otherwise the
--  same keys would be counted twice.
--
--  NOTE: uidx_dictionary_key_app_source is UNIQUE (key, application_code,
--  source), so ONE key in ONE application may legitimately have SEVERAL
--  rows -- website AND mobile, plus a NULL-source row. That is not a
--  duplicate; the reconciler aggregates them and never picks one. Export
--  every row.
--
--  HOW TO RUN: as-is for all applications, or uncomment the filter to
--  export one application at a time.
-- =====================================================================
SELECT d.application_code,
       d.key,
       d.source::text  AS source,      -- backoffice | website | mobile | NULL
       d.type::text    AS type,
       d.is_generic,
       d.description
FROM public.dictionary d
WHERE d."deletedAt" IS NULL            -- soft-deleted rows are not real keys
--AND d.application_code = 'kz'        -- <<< 'am' | 'cy' | 'kz' | 'ru' | 'uz'
ORDER BY d.application_code, d.key, d.source;
