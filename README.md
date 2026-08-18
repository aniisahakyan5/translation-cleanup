# translation-cleanup

Reconciles translation keys **per `application_code`** across five inputs:
web hardcoded usage, mobile hardcoded usage, backoffice usage, the
non-dictionary database sources, and the Dictionary.

The unit of comparison is always `(application_code, key)` — never `key`
alone. Each application is reduced to its own indexes and reconciled from
those alone, so a kz result cannot influence an am result.

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m trkeys -c config.json
```

Output: `out/translation-reconciliation.xlsx`.

---

## What the data actually looks like

Findings from inspecting the schema and the three repositories, because
several of them contradict the obvious assumption.

### `dictionary.source` is the "current source", and it has three values

```sql
dictionary_source_enum = backoffice | website | mobile
```

Anything written to `expected_source` must be one of these three, or it
cannot be stored back. That is why the code uses `website` rather than
`web`, and `backoffice` rather than `backend`.

It is also mostly empty, which is the point of the exercise:

| app | backoffice | website | mobile | **NULL** |
|-----|-----------:|--------:|-------:|---------:|
| kz  | 1324 | 275 | 106 | **6228** |
| cy  |  831 | 234 |  83 | **5270** |
| am  |  780 |  88 |  16 | **1854** |
| ru  |    0 |   0 |   0 | **7780** |
| uz  |    9 |   0 |   0 |   **18** |
| jm  |    0 |   0 |   0 |   **12** |

### The backend hardcodes no translation keys at all

`~/Desktop/app` contains **zero** `t('literal')` calls. Every backend key
reference is dynamic — `recipient.country.key.name` reads the `key` jsonb
column off a row at runtime. Grepping it for dotted literals returns ORM
relation paths (`order.customer`, `user.email`), not translation keys.

`backoffice` is not a code-grep source. It means *"this key was created
from backoffice-managed database content"*. The evidence: of kz's 1324
`backoffice` rows, 1062 trace to `notification_template` alone, while 0 of
the `website`, `mobile` or NULL rows do.

So a key is **used by backoffice exactly when the non-dictionary DB sources
reference it** — which is what `sql/db_keys.sql` returns. A `--backend-keys`
file, if you have one, is unioned in on top.

### Hardcoded keys carry no `application_code`

There is one shared web bundle and one shared mobile bundle for all six
tenants. `t('customer.balance')` ships to kz, am, cy, jm, ru and uz alike;
nothing in the source says which tenant a key belongs to. Your own
`app/scripts/key-audit/01-schema.sql` records the same shape:

```sql
CREATE TABLE keyaudit.used_static (source text, key text);  -- no app code
```

This is the one place the spec cannot be taken literally, so it is a
setting — see **Scoping** below.

---

## Scoping

`"scoping": "existing"` (default)

Hardcoded usage is credited to an application **only where the key exists in
that application's DB or Dictionary rows**. This reproduces spec section 6:

```
kz: customer.balance in kz Dictionary + web hardcoded  -> USED
am: customer.balance not in am data                    -> no am row
```

Under this mode a key inside an application's universe is by construction
present in its DB or Dictionary, so `MISSING_IN_DATABASE` cannot arise
per-application. It arises for keys the code references that **no**
application has ever heard of — reported once under `(none)` rather than
duplicated six times.

`"scoping": "global"`

The literal reading: every hardcoded key counts as used for every
application. Much noisier — all six applications report the same dangling
keys — but available with `--scoping global`.

---

## Status rules

Evaluated in this order. The order is asserted in `tests/`.

| # | Condition | Status |
|---|-----------|--------|
| 1 | no web/mobile/backoffice usage | `UNUSED` |
| 2 | recorded sources == expected sources | `OK` |
| 3 | usage spans >1 source, not fully recorded | `MULTIPLE_SOURCES` |
| 4 | used, no source recorded | `SOURCE_MISSING` |
| 5 | used, one source recorded, wrong one | `SOURCE_MISMATCH` |
| — | used but in no application's data | `MISSING_IN_DATABASE` |

Two rules are worth explaining:

**Usage outranks source problems.** A key nothing references is `UNUSED`
even if its source is NULL — there is no correct source for something
nothing uses. A NULL source is never read as "unused" on its own.

**Multi-source outranks `SOURCE_MISSING`.** Spec section 8 example 3 is a
web+mobile key with no source recorded, and it is `MULTIPLE_SOURCES`. The
remediation genuinely differs: these need one dictionary row per source, not
one value filled in. Picking a single source would be the arbitrary choice
the spec forbids.

**Multiple sources are not automatically a conflict.** The unique index is
`(key, application_code, source)`, so one key in one application may
legitimately hold several rows — `website` *and* `mobile`, plus a NULL. A
key used by web and mobile that already has both rows is `OK`.

---

## Inputs

Every input is auto-derived, and every one can be overridden with a file.

| Input | Default | Override |
|-------|---------|----------|
| DB sources | `sql/db_keys.sql` via psql | `--db-keys FILE` |
| Dictionary | `sql/dictionary.sql` via psql | `--dictionary FILE` |
| Web | scan of `~/Desktop/front` | `--web-keys FILE` |
| Mobile | scan of `~/Desktop/mobile` | `--mobile-keys FILE` |
| Backoffice | presence in DB sources | `--backend-keys FILE` |

CSV column names are **detected, not assumed** (`key` / `key_value` /
`translation_key`, `application_code` / `app_code` / …). An unresolvable
column raises rather than silently reconciling nothing. Comma, semicolon and
tab delimiters are all accepted. A bare one-key-per-line list works too.

`--dump-inputs DIR` writes the resolved inputs back out as CSV so you can
audit exactly what was compared.

### `dictionary` is excluded from the DB query

`sql/db_keys.sql` is generated from `translation-keys-per-application.sql`
with the `dictionary` branch removed — 37 branches in, 36 out. The
Dictionary is loaded separately from `sql/dictionary.sql`, so the same
information is never counted twice. The CSV loader also drops any row whose
`source_table` is `dictionary`, in case a supplied file still contains them.

All three scopes from the original query are preserved per row:
`direct` (25 tables), `relation` (11 tables, resolved through FK paths) and
`global` (`country`).

### Multiple DB rows per key are preserved

A key appearing in several tables keeps all of them, aggregated rather than
collapsed:

```
db_source_table  = notification_template,page
db_source_column = name,title
details          = scope=direct
```

---

## Output

`SUMMARY`, `ALL_KEYS`, `SOURCE_MISMATCH` (mismatch + missing), `UNUSED`,
`MISSING_KEYS`, `MULTIPLE_SOURCES`, `RUN_NOTES`.

The filtered sheets are views over the same rows as `ALL_KEYS`, not
recomputations, so they cannot disagree with it. `RUN_NOTES` records what
was actually loaded — row counts, repository paths, scoping mode — so a
report is self-describing.

Status colouring uses conditional-formatting rules rather than per-cell
fills; styling cells individually costs an `ws.max_row` lookup per row,
which is O(n) each and makes a 36k-row sheet take four minutes.

---

## Usage

```bash
# every application
./.venv/bin/python -m trkeys -c config.json

# one application
./.venv/bin/python -m trkeys -c config.json -a kz

# from files instead of live sources
./.venv/bin/python -m trkeys \
    --db-keys db.csv --dictionary dict.csv \
    --web-keys web.txt --mobile-keys mobile.txt

# audit what was compared
./.venv/bin/python -m trkeys -c config.json --dump-inputs out/inputs

./.venv/bin/python -m unittest discover -s tests
```

`config.json` points at the database command and the repository paths.
The DB command is any argv accepting SQL on stdin, so a plain
`["psql", "-U", "movato", "-d", "movato"]` works as well as `docker exec`.

---

## Caveats

- **`ru` has no sources set at all** (7780 NULL). Its `SOURCE_MISSING` count
  is therefore ~everything, and its `MULTIPLE_SOURCES` is 0 only because the
  web/mobile keys are not in the ru dictionary.
- **Key extraction is regex-based**, so it only sees literal keys. There are
  no interpolated `` t(`a.${x}`) `` calls in either repository, but there are
  variable calls — `t(group.key.name)`, `t(label)`. Those read a `key` jsonb
  column at runtime, so the keys behind them arrive through the DB side of
  the reconciliation instead, and are not lost. Patterns are configurable.
- **Some extracted "keys" are literal English strings** — `Go to my
  location`, `Failed to fetch address information`. These are real
  `t('...')` calls using text as the key. They surface in `MISSING_KEYS`,
  which is correct: they have no dictionary entry.
- **`is_generic` and `type` are carried but not used** in any rule.

Table coverage was verified: 37 tables in `public` have a `key` column,
`sql/db_keys.sql` reads 36 of them, and the only omission is `dictionary`.
