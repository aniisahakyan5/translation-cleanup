-- =====================================================================
--  db_keys.sql  --  INPUT 1 of 2: the non-dictionary key sources.
--
--  This is your original translation-keys-per-application.sql with the
--  `dictionary` branch removed -- 37 branches in, 36 out.
--
--  WHY the dictionary branch is gone: the dictionary is loaded separately
--  by dictionary.sql, because THIS query cannot supply it. Its dictionary
--  branch returned only the key, never dictionary.source -- and `source`
--  is the column that source_missing / source_mismatch are measured
--  against. Keeping both would also count the same keys twice.
--
--    scope = direct   (25 tables) - src has its own application_code
--            relation (11 tables) - src reaches app through a FK path
--            global   ( 1 table ) - country: shared by every application
--
--  HOW TO RUN -- either way works, the reconciler accepts both:
--
--    a) All applications in one go: run as-is, one file out.
--    b) One application at a time, as you do today: uncomment the
--       application_code filter at the bottom and run it once per code.
-- =====================================================================
WITH raw AS (
    SELECT app.code AS application_code, 'application_configuration' AS source_table, 'direct' AS scope,
           NULL::text AS source_column, to_jsonb(src."key") AS raw_value
    FROM public.application app
    JOIN public.application_configuration src ON src.application_code::text = app.code
    UNION ALL
    SELECT app.code AS application_code, 'banner' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.banner src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'customs_region' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.customs_region src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'delivery_area' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.delivery_area src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'delivery_configuration' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.delivery_configuration src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'delivery_courier' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.delivery_courier src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'document_type' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.document_type src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'locker' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.locker src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'membership' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.membership src ON src.application_code::text = app.code
                       AND src.deleted_at IS NULL
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'news' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.news src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'notification_template' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.notification_template src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'page' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.page src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'payment_provider' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.payment_provider src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'product_category' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.product_category src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'product_shop' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.product_shop src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'question' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.question src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'reason' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.reason src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'restricted_product' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.restricted_product src ON src.application_code::text = app.code
                       AND src.deleted_at IS NULL
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'shipping_condition' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.shipping_condition src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'shop_category' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.shop_category src ON src.application_code::text = app.code
                       AND src.deleted_at IS NULL
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'social_network' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.social_network src ON src.application_code::text = app.code
                       AND src.deleted_at IS NULL
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'special_service' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.special_service src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'transaction_type' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.transaction_type src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'tutorial' AS source_table, 'direct' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.tutorial src ON src.application_code::text = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'application' AS source_table, 'relation' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.application src ON src.code = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'city' AS source_table, 'relation' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.city src ON EXISTS (SELECT 1 FROM public.region r
                            WHERE r.id = src.region_id
                              AND r.country_code = app.code)
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'competition_category' AS source_table, 'relation' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.competition_category src ON EXISTS (SELECT 1 FROM public.competition c
                            WHERE c.competition_category_id = src.id
                              AND c.application_code = app.code)
                       AND src.deleted_at IS NULL
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'currency' AS source_table, 'relation' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.currency src ON EXISTS (SELECT 1 FROM public.ref_application_currency x
                            WHERE x.currency_code = src.code
                              AND x.application_code = app.code)
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'district' AS source_table, 'relation' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.district src ON EXISTS (SELECT 1 FROM public.city c
                            JOIN public.region r ON r.id = c.region_id
                            WHERE c.id = src.city_id
                              AND r.country_code = app.code)
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'language' AS source_table, 'relation' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.language src ON EXISTS (SELECT 1 FROM public.ref_application_language x
                            WHERE x.language_code = src.code
                              AND x.application_code = app.code)
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'membership_advantage' AS source_table, 'relation' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.membership_advantage src ON EXISTS (SELECT 1 FROM public.membership m
                            WHERE m.id = src.membership_id
                              AND m.application_code = app.code)
                       AND src.deleted_at IS NULL
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'region' AS source_table, 'relation' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.region src ON src.country_code = app.code
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'shop' AS source_table, 'relation' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.shop src ON EXISTS (SELECT 1 FROM public.ref_shop_application x
                            WHERE x.shop_id = src.id
                              AND x.application_code = app.code)
                       AND src.deleted_at IS NULL
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'tracking_point' AS source_table, 'relation' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.tracking_point src ON EXISTS (SELECT 1 FROM public.shipping_condition sc
                            WHERE sc.id = src.shipping_condition_id
                              AND sc.application_code = app.code)
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'warehouse' AS source_table, 'relation' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.warehouse src ON (src.type::text = 'destination' AND src.country_code = app.code)
                    OR EXISTS (SELECT 1 FROM public.shipping_condition     x WHERE x.warehouse_id = src.id AND x.application_code = app.code)
                    OR EXISTS (SELECT 1 FROM public.delivery_configuration x WHERE x.warehouse_id = src.id AND x.application_code = app.code)
                    OR EXISTS (SELECT 1 FROM public.delivery_area          x WHERE x.warehouse_id = src.id AND x.application_code = app.code)
                    OR EXISTS (SELECT 1 FROM public.locker                 x WHERE x.warehouse_id = src.id AND x.application_code = app.code)
    CROSS JOIN LATERAL jsonb_each(src."key") kv
    UNION ALL
    SELECT app.code AS application_code, 'country' AS source_table, 'global' AS scope,
           kv.key    AS source_column, kv.value AS raw_value
    FROM public.application app
    JOIN public.country src ON TRUE
    CROSS JOIN LATERAL jsonb_each(src."key") kv
)
SELECT k.application_code,
       k.source_table,
       k.source_column,
       k.raw_value #>> '{}' AS key_value,
       k.scope
FROM raw k
-- one place to guard the json payload for all 36 branches
WHERE jsonb_typeof(k.raw_value) = 'string'
  AND btrim(k.raw_value #>> '{}') <> ''
--AND k.application_code = 'kz'        -- <<< 'am' | 'cy' | 'kz' | 'ru' | 'uz'
ORDER BY k.application_code, k.source_table, key_value;
