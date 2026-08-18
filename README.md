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

## The DB files decide which applications are reconciled

Drop in `kz.tsv` and only kz is reconciled. Drop in `kz` and `am` and both
are. The application set comes from the `application_code` values in the DB
exports -- nothing has to be configured.

A dictionary covering applications you did not export is **not** evidence
those applications are dead; it is evidence you did not export them. They
are skipped and named, rather than reported as entirely unused:

```
note:
   am, cy, ru, uz SKIPPED -- dictionary rows but no DB sources. Only the
   applications present in the DB files are reconciled.
```

This is reported as a note, not a warning -- it is a deliberate consequence
of what was supplied. Genuine problems (DB rows with no dictionary, no
web/mobile keys loaded) still say CHECK YOUR INPUTS.

`-a/--application` still overrides the set explicitly. With no DB export at
all, the dictionary supplies the set so a dictionary-only run still works.

Verified: kz reports the same 9725/1495/6592/123/169 whether one, two or
five DB files are supplied.

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

## Every row carries its fix

`action` sits beside `status` and states the edit to make against
`public.dictionary` -- the only table any of these verdicts can be fixed in.

| status | action |
|---|---|
| `UNUSED` | `delete from dictionary` |
| `SOURCE_MISSING` | `set source = website` |
| `SOURCE_MISMATCH` | `change source website → mobile` |
| `MULTIPLE_SOURCES` | `add row for website,mobile` |
| `MULTIPLE_SOURCES` (partly recorded) | `add row for mobile` |
| `MULTIPLE_SOURCES` (stale row too) | `add row for website,mobile; remove row for backoffice` |
| `MISSING_IN_DATABASE` | `add to dictionary with source = website` |
| `OK` | empty |

`delete from dictionary` is safe on `UNUSED` by construction: backoffice
usage means "referenced by DB content", so a key with DB rows can never be
unused. What is left is a dictionary row nothing references.

Multi-source rows never collapse to one source -- they ask for a row per
source, because that is what the unique index allows and picking one would
be the arbitrary choice the spec forbids.

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
| DB sources | `sql/db_keys.sql` via psql | `--db-keys FILE` (repeatable/globbable) |
| Dictionary | `sql/dictionary.sql` via psql | `--dictionary FILE` (repeatable/globbable) |
| Web | scan of `~/Desktop/front` | `--web-keys FILE` |
| Mobile | scan of `~/Desktop/mobile` | `--mobile-keys FILE` |
| Backoffice | presence in DB sources | `--backend-keys FILE` |

CSV column names are **detected, not assumed** (`key` / `key_value` /
`translation_key`, `application_code` / `app_code` / …). An unresolvable
column raises rather than silently reconciling nothing. Comma, semicolon and
tab delimiters are all accepted. A bare one-key-per-line list works too.

`--dump-inputs DIR` writes the resolved inputs back out as CSV so you can
audit exactly what was compared.

### Per-application exports

The original query is run once per application, so production yields one
file per application rather than one combined file. Both `--db-keys` and
`--dictionary` are repeatable and accept globs:

```bash
python -m trkeys \
    --db-keys 'exports/keys_*.tsv' \
    --dictionary 'exports/dict_*.tsv' \
    --web-keys web.txt --mobile-keys mobile.txt \
    --split -o report.xlsx
```

Files may be CSV or TSV; the delimiter is detected. Each file's own
`application_code` column decides which application its rows belong to, so
the files can be dropped in any order. If a per-application export has had
that column projected away, the code is recovered from the filename
(`keys_kz.tsv` -> `kz`) rather than dropping the rows.

**Feed the query's output in unchanged.** It still contains its
`dictionary` branch; those rows are dropped on load, because the Dictionary
arrives separately and counting both would double count it.

**The query cannot supply the Dictionary.** Its dictionary branch selects
only the key, never `dictionary.source` -- and `source` is what
`SOURCE_MISSING`, `SOURCE_MISMATCH` and `expected_source` are measured
against. Export it separately with `sql/dictionary.sql`. A dictionary file
with no `source` column is rejected rather than reported as
"every key is missing its source".

### One report per application

`--split` writes one workbook per application_code instead of a single
combined one:

```
report-am.xlsx  report-cy.xlsx  report-kz.xlsx
report-ru.xlsx  report-uz.xlsx  report-unmapped.xlsx
```

Each contains only that application's rows -- asserted in testing, no
workbook holds a second application code. `report-unmapped.xlsx` holds the
keys that are referenced in code but belong to no application; they are
listed once rather than repeated in every file.

Each workbook's SUMMARY row comes from the combined pass, so
`multi_application_keys` still means "also present in another application";
recomputing it per file could only ever report 0.

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

## Hardcoded keys come from the repositories

The three source repositories are cloned and scanned directly; there are no
key files to produce by hand.

| source | repository | branch | scanned |
|---|---|---|---|
| `website` | `Movato/front` | `main` | `app/`, `.ts .tsx .js .jsx` |
| `mobile` | `Movato/mobile` | `main` | `src/`, `.ts .tsx .js .jsx` |
| `backoffice` | `Movato/app` | `main` | `libs/`, `apps/`, `.ts` |

Clones are shallow, single-branch, and kept in `.cache/repos` (gitignored).
A run refreshes them with fetch + hard reset rather than pull, so a
rewritten branch cannot leave a conflicted tree. `--no-fetch` scans the
cache as-is, offline. Each run records the commit it scanned:

```
website: 648 unique keys scanned from .cache/repos/website @ 6023b5c 2026-07-20
```

**Branch matters.** All three are pinned to `main` in `config.json`, so a
run is reproducible rather than following whatever the default happens to
be. A feature branch can hold keys `main` does not:

```bash
--ref website=add-cy-domain     # 588 keys @ e9aad9c
                                # vs 648 @ 6023b5c on main
```

### The Update button

`--serve` runs the page with a local backend, so scanning happens when you
ask for it rather than on every visit:

```bash
python -m trkeys -c config.json --serve      # http://127.0.0.1:8765
```

Opening the page reads the last scan from `.cache/keys.json` and fills the
three key slots immediately -- 0.03s, no network. **Update keys from
GitHub** re-clones and re-scans, about 5 seconds, and rewrites the cache.
The cache survives restarts, so nothing contacts GitHub until you press the
button.

Bound to loopback only: it serves the contents of your source
repositories and has no authentication. Concurrent scans are refused with
409 rather than allowed to fight over the same working tree.

Without a backend -- the page opened as a plain file, or published as an
artifact -- the probe fails silently, the button stays hidden, and the page
works exactly as before with files dropped in by hand.

### Producing the key files for the browser app

The browser cannot clone repositories, so scan them once and drop the
results into the matching slots. No database is touched:

```bash
python -m trkeys -c config.json --export-keys out/keys
```

```
wrote out/keys/website_keys.csv       648 keys
wrote out/keys/mobile_keys.csv        335 keys
wrote out/keys/backoffice_keys.csv      0 keys
```

### The backend scan finds nothing, and that is correct

`Movato/app` yields **0** keys across 3,627 scanned `.ts` files. It does not
hardcode translation keys -- it reads them from `key` jsonb columns at
runtime. Backoffice usage is therefore driven by DB content: reconcile.py
credits a key to backoffice when the non-dictionary DB sources reference it,
whatever the scan finds. Anything the scan does find is unioned on top, so
the day the backend starts hardcoding keys, they are picked up.

---

## Input coverage is checked

The inputs must cover the same applications. An application with dictionary
rows but no DB rows still produces a full set of plausible rows, every one
marked `UNUSED` -- indistinguishable from a real cleanup finding. Loading
one application's DB export against a five-application dictionary therefore
looks like "four applications are entirely dead" rather than like a mistake.

Both the CLI and the browser app now refuse to be quiet about it:

```
!! CHECK YOUR INPUTS -- these results are probably misleading
!! am, cy, ru, uz in the dictionary but NOT in the DB sources -- every key
   for them will read as unused. Run the query for am, cy, ru, uz too.
!! no web keys loaded -- nothing can be marked used in web
```

The browser app shows the same as a banner and tags each affected
application card with **no db data**. Tests in `web/test-coverage.mjs`.

The tell-tale signature of a partial load: `MULTIPLE_SOURCES` is 0 for every
application, and whole applications are 100% `UNUSED` with 0 source
problems. A key can only reach `MULTIPLE_SOURCES` when two usage sources are
loaded at once.

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

---

## Browser app

`web/index.html` is a self-contained page that does the same reconciliation
client-side — drop the CSVs in, filter the results, export what you select.
Nothing is uploaded; it runs entirely in the browser.

Generate the inputs, then drag them onto the page:

```bash
./.venv/bin/python -m trkeys -c config.json --dump-inputs out/inputs
```

Files are matched to slots by filename, falling back to column detection.
Only `db_keys.csv` and `dictionary.csv` are required.

Filters: application, status, used-in web/mobile/backoffice, exists-in
dictionary/DB, and full-text search over key, source table and details.
Clicking a status count in the summary filters to it. `Export CSV` writes
the current filtered view.

`web/verify.mjs` runs the page's reconciliation under node against the same
CSVs so it can be checked against the python implementation:

```bash
node web/verify.mjs out/inputs
```

Both produce identical per-application counts.
