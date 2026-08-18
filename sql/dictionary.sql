-- =====================================================================
--  dictionary.sql  --  the authoritative Dictionary input.
--
--  public.dictionary is the ONLY source of dictionary keys. It is
--  deliberately excluded from db_keys.sql so the two never double count.
--
--  NOTE: uidx_dictionary_key_app_source is UNIQUE (key, application_code,
--  source) -- one key in one application may legitimately carry SEVERAL
--  rows with different sources (e.g. website AND mobile), plus a NULL
--  source row. The reconciler aggregates them; it never picks one.
-- =====================================================================
SELECT d.application_code,
       d.key,
       d.source::text  AS source,
       d.type::text    AS type,
       d.is_generic,
       d.description
FROM public.dictionary d
WHERE d."deletedAt" IS NULL
ORDER BY d.application_code, d.key, d.source;
