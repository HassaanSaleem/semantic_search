# Semantic Search — Module Spec

> **Goal**: package `semantic_search` as a reusable module (e.g. an in-tree shared module at `src/shared/semantic_search/`). External dependency packages (`sql_repository`, `events_repository`, `bigquery_repository`) are consumed as-is by their import names — swap those imports if/when the shared modules for SQL/BigQuery/Events also get renamed.

---

## 1. Purpose

A reusable component that:

1. **Scans tabular metadata** from either a PostgreSQL schema (live introspection) or a pre-aggregated BigQuery table (`tenant metadata`).
2. **Transforms** that metadata into natural-language **statements / stories** (deterministic templates).
3. **Loads** those statements into a **Weaviate** collection — either with manual SentenceTransformer embeddings *or* by configuring a Weaviate-managed vectorizer (e.g. `text2vec-openai`).
4. **Queries** the collection via near-vector or hybrid search, optionally filtered by `tenant_id`, `source`, and `has_funnel_stage`.
5. **Reverses** statements back into a structured request and **fuzzy-matches** user-supplied values against known categorical values from the underlying DB (the "schema-fix" flow).

Two pipelines exist; pick at runtime depending on what data source you're scanning:

| Pipeline | Source | Statement generator |
|---|---|---|
| **Postgres → Weaviate** | live `information_schema` introspection per table/column | `SchemaTransformer.prepare_statements_from_database` |
| **BigQuery → Weaviate** | rows from `<project>.<dataset>.<table>` (the `tenant metadata` table) | `SchemaTransformer.prepare_statements_from_bigquery` (event / profile / billing variants) |

---

## 2. Target Layout

```
src/shared/semantic_search/
├── __init__.py                  # public re-exports
├── utils.py
├── schema_scanner.py            # PostgresSchemaScanner
├── bigquery_metadata_scanner.py # BigQueryMetadataScanner
├── schema_transformer.py        # SchemaTransformer
├── weaviate_loader.py           # WeaviateLoader
└── semantic_search.py           # SemanticSearch orchestrator
```

`__init__.py` re-exports: `simplify_date`, `infer_value_type`, `similarity_score`, `find_best_match`, `PostgresSchemaScanner`, `BigQueryMetadataScanner`, `SchemaTransformer`, `WeaviateLoader`, `SemanticSearch`.

Internal imports use `from src.shared.semantic_search.<mod> import ...` (or the project's standard relative-import style — match neighboring shared modules).

---

## 3. External Dependencies

These are **not** rewritten — they're consumed as-is via existing project dependencies:

| Symbol | Source pkg | Used in |
|---|---|---|
| `AsyncReadRepository` | `sql_repository` | `PostgresSchemaScanner`, `SemanticSearch` (typing + `.execute(query) -> list[tuple]`) |
| `BigQueryReadRepository` | `bigquery_repository` | `BigQueryMetadataScanner` (`.select_all(query, parameters=dict) -> list[dict]`) |
| `DatetimeValidator` | `events_repository` | `utils.simplify_date`, `utils.infer_value_type` (uses `.is_valid(value) -> bool` and `.from_value(value) -> datetime|date|None`) |
| `weaviate-client==4.11.1` | pip | `WeaviateLoader`, `SemanticSearch`, examples |
| `sentence-transformers` | pip | `WeaviateLoader` (loads `all-MiniLM-L6-v2`, 384-dim) |

Required runtime dependencies (from `pyproject.toml`):
- `weaviate-client>=4.11`
- `sentence-transformers>=2.2`
- `bigquery_repository` + `google-cloud-bigquery>=3.35` (only when using the BigQuery path / examples; the companion adapter is not yet published, install it from source)

---

## 4. Module: `utils`

Pure functions. No I/O.

### `infer_value_type(value) -> tuple[str, Any]`
Classify a raw value and return `(type, transformed_value)`. Order of checks matters:

1. Try `float(value)` → `("numeric", float_val)`.
2. Else lowercase-strip; if `"true"` or `"false"` → `("boolean", lower_str_val)`.
3. Else `DatetimeValidator.is_valid(value)` → `("date", DatetimeValidator.from_value(value))`.
4. Else → `("text", value)` (original, unmodified).

### `simplify_date(date_str: str) -> str`
Parse via `DatetimeValidator.from_value`. If `None`, return original string.
- `datetime` → `strftime("%Y-%m-%d")`
- `date` → `isoformat()`
- else → `str(dt_obj)`

### `similarity_score(a: str, b: str) -> float`
`SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100.0` — returns 0.0–100.0.

### `find_best_match(target: str, candidates: list[str]) -> tuple[str, float]`
Linear scan, returns `(best_candidate, best_score)`. Initial `("", 0.0)` if `candidates` is empty (no match). Uses **strictly greater than** for replacement (first equal-best wins).

---

## 5. Module: `schema_scanner` — `PostgresSchemaScanner`

Async PostgreSQL introspection over `information_schema`. Used for the Postgres → Weaviate path.

### Constructor
```python
PostgresSchemaScanner(
    sql_repository: AsyncReadRepository,
    schema: str = "public",
    max_values: int = 10,
    exclude_columns: Optional[List[str]] = None,
)
```

### Type categorization (lowercase compares)
| Bucket | Postgres data types |
|---|---|
| `text_data_types` | `character varying`, `text`, `varchar` |
| `numeric_data_types` | `integer`, `bigint`, `smallint`, `decimal`, `numeric`, `real`, `double precision` |
| `boolean_data_types` | `boolean` |
| `date_data_types` | `date`, `timestamp`, `timestamp without time zone`, `timestamp with time zone`, `time`, `time without time zone`, `time with time zone` |

Anything outside this union is silently skipped. Columns in `exclude_columns` are also skipped.

### Methods (all async; all swallow exceptions, log via module logger, return safe empties)

| Method | Returns | SQL it issues |
|---|---|---|
| `list_tables()` | `list[str]` | `SELECT table_name FROM information_schema.tables WHERE table_schema = '<schema>' AND table_type = 'BASE TABLE';` |
| `list_columns_and_types(table)` | `list[tuple[str, str]]` filtered to supported types & not-excluded | `SELECT column_name, data_type FROM information_schema.columns WHERE table_schema=... AND table_name=...;` |
| `fetch_text_distinct_values(table, col)` | `list[str]` (≤ `max_values`) | `SELECT DISTINCT "<col>" FROM "<schema>"."<table>" WHERE "<col>" IS NOT NULL LIMIT <max_values>;` |
| `fetch_numeric_stats(table, col)` | `{"mean": float, "std": float}` (zeros if null/empty) | `SELECT AVG, STDDEV_POP ...` |
| `fetch_boolean_distinct_values(table, col)` | `list[str]` | `SELECT DISTINCT ... WHERE NOT NULL;` |
| `fetch_date_stats(table, col)` | `{"min": str|None, "max": str|None}` | `SELECT MIN, MAX ...` |
| `get_table_column_data(table)` | `dict[str, dict]` shape per column type (see below) | composes the above |

Per-column shape returned by `get_table_column_data`:
- text → `{"type": "text", "values": [...]}` (only if `values` is non-empty)
- numeric → `{"type": "numeric", "mean": float, "std": float}` (always present)
- boolean → `{"type": "boolean", "values": [...]}` (only if non-empty)
- date → `{"type": "date", "min": str|None, "max": str|None}` (always present, even if both None)

> **SQL injection note**: queries are built via f-string interpolation of `schema`/`table_name`/`column_name`. Acceptable because callers pass schema-controlled identifiers (read from `information_schema`), not user input. The rewrite should preserve this contract — do NOT pass user-supplied table/column names without validation.

---

## 6. Module: `bigquery_metadata_scanner` — `BigQueryMetadataScanner`

Synchronous (not async) — `BigQueryReadRepository.select_all` is sync.

### Constructor
```python
BigQueryMetadataScanner(
    bq_read_repository: BigQueryReadRepository,
    table_id: str,  # e.g. "analytics.example_dataset.tenant_metadata"
)
```

### `get_metadata_for_tenant(tenant_slug: str, use_distinct: bool = True) -> list[dict]`

Returns all rows for a tenant. `select_all` is invoked with parameter binding `{"tenant_slug": tenant_slug}` against `@tenant_slug`. On exception, logs and returns `[]`.

Two query shapes:

**`use_distinct=True`** (recommended): explicit `SELECT DISTINCT` of these columns to dedupe across `computed_at`:
```
tenant_slug, source, event_name, event_description, event_tag,
property_name, property_description, property_score, property_tag,
top_values, numeric_mean, numeric_stddev, date_min, date_max,
cardinality_band, funnel_stage, metric, correlation_value
```

**`use_distinct=False`**: `SELECT * ... ORDER BY computed_at DESC`.

### Source row contract (consumed downstream by `SchemaTransformer`)

`source` ∈ `{"event", "profile", "billing"}`. Other values are skipped (warning logged).

`top_values` and `funnel_stage` are BigQuery `REPEATED` fields — Python receives them as `list`.

---

## 7. Module: `schema_transformer` — `SchemaTransformer`

Stateless apart from `database` and `schema` strings used in Postgres-path templates.

### Constructor
```python
SchemaTransformer(database: str, schema: str)
```
For the BigQuery path, both can be empty strings.

### 7.1 Postgres path: `prepare_statements_from_database(table_name, columns_data) -> list[str]` (async)

Produces one statement per text/boolean value, **one** statement for numeric/date columns. Templates (exact wording — used by the inverse parser):

| Type | Template |
|---|---|
| text | `Database {db} has a Schema {schema}, Table {table}, Column {col}, containing distinct text values {value}.` (one per value) |
| numeric | `Database {db} has a Schema {schema}, Table {table}, Column {col}, with mean {mean:.2f} and standard deviation {std:.2f}.` |
| boolean | `Database {db} has a Schema {schema}, Table {table}, Column {col}, containing distinct boolean values {v1, v2, ...}.` (comma-joined, single statement) |
| date | `Database {db} has a Schema {schema}, Table {table}, Column {col}, with date range from {min} to {max}.` (skipped if both None) |

### 7.2 Request → statements: `prepare_statements_from_request(request, database="analytics", table_name="") -> list[str]` (async)

`request` shape:
```python
{
    "data_api": "<schema>",   # default "public"
    "parameters": { "<col>": <raw_value>, ... },
}
```

For each `(col, raw_val)`, run `infer_value_type` and emit the matching template. Caveat already in code: the **numeric** template here ends `…and standard deviation.` (no value) — preserve verbatim if downstream parsers depend on it; otherwise normalize to match 7.1. Recommend documenting this asymmetry but **not silently changing it** during the rewrite without grepping callers first.

### 7.3 Statements → request: `prepare_request_from_statements(statements) -> dict` (async)

Inverse of 7.1. Compiles four regexes (text / numeric / boolean / date) and builds:
```python
{
    "data_api": <first parsed schema or "">,
    "parameters": {
        "<col>": {
            "table": "<table>",
            # text/boolean: "values": [v1, v2, ...]
            # numeric:      "mean": float, "std": float
            # date:         "min": "<simplified>", "max": "<simplified>"
        }, ...
    },
    "operator": "and",
}
```

Date min/max are passed through `simplify_date`. Unparseable lines log a warning and are skipped.

Regex patterns (must round-trip with §7.1 exactly):
```
text:    ^Database\s+(?P<db>\S+)\s+has a Schema\s+(?P<schema>\S+),\s+Table\s+(?P<table>\S*?),\s+Column\s+(?P<col>\S+),\s+containing distinct text values\s+(?P<value>.*)\.$
numeric: ^Database\s+(?P<db>\S+)\s+has a Schema\s+(?P<schema>\S+),\s+Table\s+(?P<table>\S*?),\s+Column\s+(?P<col>\S+),\s+with mean\s+(?P<mean>[\d\.]+)\s+and standard deviation\s+(?P<std>[\d\.]+).*
boolean: ^Database\s+(?P<db>\S+)\s+has a Schema\s+(?P<schema>\S+),\s+Table\s+(?P<table>\S*?),\s+Column\s+(?P<col>\S+),\s+containing distinct boolean values\s+(?P<value>.*)\.$
date:    ^Database\s+(?P<db>\S+)\s+has a Schema\s+(?P<schema>\S+),\s+Table\s+(?P<table>\S*?),\s+Column\s+(?P<col>\S+),\s+with date range from\s+(?P<min_val>.+?)\s+to\s+(?P<max_val>.+)\.$
```

### 7.4 BigQuery path: `prepare_statements_from_bigquery(tenant_slug, rows) -> list[dict]` (async)

Returns a flat list of `{"statement": str, "tenant_id": tenant_slug, "source": "event"|"profile"|"billing", "has_funnel_stage"?: True}`.

Dispatches on `row["source"]`:

#### `_generate_event_stories(tenant_slug, row)` — source `"event"`
- Skip row entirely if `event_name` is falsy.
- Build a single sentence by appending parts in order:
  1. `Tenant {tenant_slug} has Event {event_name}`
  2. if `event_tag`: `with tag {event_tag}`
  3. if `funnel_stage` is a non-empty list with at least one truthy stage: `in funnel {", ".join(valid_stages)}` and set `has_funnel_stage=True`
  4. if `property_name`: `with property {property_name}`
  5. value clause (mutually exclusive, in this order):
     - categorical: `top_values` is list AND `numeric_mean is None` AND first 5 truthy → `containing values like {", ".join(top_5)}`
     - numeric: `numeric_mean is not None AND numeric_stddev is not None` → `with mean {mean:.2f} and standard deviation {stddev:.2f}`
     - date: `date_min OR date_max` truthy → `with date range from {date_min} to {date_max}`
- Always emit a single dict (events emit even without a value clause). Add `has_funnel_stage: True` only when it applies; otherwise omit the key.

#### `_generate_profile_stories(tenant_slug, row)` — source `"profile"`
- Skip if `property_name` falsy.
- Skip the **whole row** if categorical (`top_values` list, `numeric_mean is None`) AND `cardinality_band.lower() in {"high", "extreme"}`. Log info.
- Build:
  1. `Tenant {tenant_slug} has Profile Property {property_name}`
  2. if `property_tag`: `with tag {property_tag}`
  3. value clause (same precedence as events).
- **REQUIRED**: only emit if a value clause was added. Otherwise return `[]`.
- No `has_funnel_stage` key.

#### `_generate_billing_stories(tenant_slug, row)` — source `"billing"`
Identical to profile but title is `has Billing Property` and the cardinality-skip log says "billing property".

---

## 8. Module: `weaviate_loader` — `WeaviateLoader`

Wraps the async Weaviate v4 client. **Loads `SentenceTransformer("all-MiniLM-L6-v2")` in the constructor** — heavy; if you need a no-vectorizer / openai-only mode, the rewrite should make this lazy.

### Constructor
```python
WeaviateLoader(
    weaviate_host: str = "127.0.0.1",
    http_port: int = 8080,
    grpc_port: int = 50051,
    api_key: str = "",
    secure: bool = False,                  # legacy; fallback for both transports
    grpc_host: str | None = None,          # defaults to weaviate_host
    http_secure: bool | None = None,       # defaults to `secure`
    grpc_secure: bool | None = None,       # defaults to `secure`
)
```

State: `_client`, `_collection`, `_model` (SentenceTransformer).

### `connect()` (async)
Uses `weaviate.use_async_with_custom(...)` with `AdditionalConfig(timeout=Timeout(init=30, query=60, insert=300))`. Sets `auth_credentials=AuthApiKey(api_key)` only if `api_key` is truthy. After `client.connect()`, polls `await client.is_ready()` with `asyncio.sleep(1)` until ready.

### `create_collection(name, description="", use_vectorizer=False, properties=None)` (async)
Default `properties = [Property(name="statement", data_type=DataType.TEXT)]` when `None`. Calls `self._client.collections.get(name)`; if no collection returned, creates with `vectorizer_config=None`. Stores on `self._collection`. Returns the collection.

> The `use_vectorizer` parameter exists in the signature but isn't actually used to switch behavior — `vectorizer_config=None` is hardcoded. Preserve this for backward compat unless the rewrite explicitly drops the param.

### `recreate_collection(name, description="", properties=None)` (async)
If collection exists, deletes it, then calls `create_collection`.

### `insert_data(statements: list[str] | list[dict])` (async)
- If list of dicts: extract `item["statement"]` for embedding; pass the **whole dict** as object properties (dict must contain `statement`; can also include `tenant_id`, `source`, `has_funnel_stage`, etc.).
- If list of strings: encode strings; properties = `{"statement": s}`.
- Uses `self._model.encode(...).tolist()` for vectors.
- Inserts via `self._collection.data.insert_many([DataObject(properties=..., vector=...), ...])`.
- Raises `RuntimeError("Collection not created or set. Call create_collection first.")` if `_collection` is None.

### `close()` (async)
Closes client, sets `_client` and `_collection` to None.

---

## 9. Module: `semantic_search` — `SemanticSearch`

Orchestrator for the **Postgres path**. (BigQuery path is currently used directly via `SchemaTransformer` + raw `weaviate` client in `examples/bigquery_example_v2.py`. The rewrite may consolidate this — see §11.)

### Constructor
```python
SemanticSearch(
    sql_read_repository: AsyncReadRepository,
    schema_scanner: PostgresSchemaScanner,
    schema_transformer: SchemaTransformer,
    weaviate_loader: WeaviateLoader,
    schema_name: str = "public",
    weaviate_collection_name: str = "SchemaStatements",
)
```

### `gather_all_column_data(tables)` (async)
For each table, schedules `schema_scanner.get_table_column_data(table)` as `asyncio.create_task`, then awaits each in order. Returns `{table: column_data}`.

### `process_schema()` (async)
1. `tables = await schema_scanner.list_tables()`
2. `table_column_data = await self.gather_all_column_data(tables)`
3. For each table, build statements via `prepare_statements_from_database`, accumulate into `all_statements`.
4. `await self.push_to_weaviate(all_statements)`
5. **Side effect**: dumps the collected column data as JSON. The path is configurable via the `output_path` constructor arg or a `process_schema(output_path=...)` override (default: `resources/table_column_data.json`); parent directories are created if missing.

### `push_to_weaviate(all_statements)` (async)
`connect()` → `recreate_collection(name=..., description="Statements from PostgreSQL schema.")` → `insert_data(...)` → `close()`. Logs the insert response.

### `semantic_search(query_text, limit=5, certainty=0.85)` (async)
- Connects; if no `_collection`, calls `create_collection(name, description="Statements from PostgreSQL schema.")`.
- Encodes query via the loader's `_model`, calls `collection.query.near_vector(near_vector=..., certainty=..., limit=..., return_metadata=MetadataQuery(certainty=True, distance=True))`.
- Returns list of `{statement, certainty, distance, score}`.
- Closes client at the end.

> Implementation note: **calling `connect()` and `close()` per query is expensive.** The rewrite should consider hoisting connection lifecycle to a context manager / startup-shutdown hook. Don't change this silently though — `process_schema` and `semantic_search_schema_fix` rely on the close-on-exit behavior.

### `semantic_search_schema_fix(request) -> tuple[dict, float]` (async)
1. `statements = await schema_transformer.prepare_statements_from_request(request)`
2. For each statement, call `semantic_search(stmt, limit=1, certainty=0.7)` and collect result `[0]`.
3. Average certainty across all returned top-1s.
4. Reverse the matched statements to a request via `prepare_request_from_statements`.
5. Pass `(request, semantic_request)` through `find_similar_values` to enrich text params with fuzzy-match metadata.
6. Return `(enriched_semantic_request, avg_certainty)`.

### `find_similar_values(request, semantic_schema) -> dict` (async)
Pairs **non-numeric** user params (in original dict order) with the schema params that have a `"values"` list (also in dict order), by index. For each pair:
- Run `find_best_match(user_val, existing_vals)`.
- If score < **70.0**, fetch all distinct values from `<schema>.<table>.<col>` via `_fetch_all_distinct_values`, union with existing, retry. Adopt the new match if it scored higher.
- Annotate the schema param with: `request_value` (str), `similarity_match` (best match), `similarity_score` (rounded to 2 decimals).

`MATCH_THRESHOLD = 70.0` — preserve this constant.

### `_fetch_all_distinct_values(table_name, column_name) -> list[str]` (async)
`SELECT DISTINCT "<col>" FROM "<schema>"."<table>" WHERE "<col>" IS NOT NULL;` via `_sql_read_repository.execute`. Same SQL-injection caveat as §5.

---

## 10. Statement formats — canonical reference

The exact strings the system produces and parses. Anything that consumes statements (LLMs, regexes, tests) should reference this section.

### Postgres-path (round-trippable via §7.3 regexes)

```
Database {db} has a Schema {schema}, Table {table}, Column {col}, containing distinct text values {value}.
Database {db} has a Schema {schema}, Table {table}, Column {col}, with mean {mean:.2f} and standard deviation {std:.2f}.
Database {db} has a Schema {schema}, Table {table}, Column {col}, containing distinct boolean values {v1}, {v2}.
Database {db} has a Schema {schema}, Table {table}, Column {col}, with date range from {min} to {max}.
```

### BigQuery-path (NOT round-trippable — these are stories, not parseable templates)

```
Tenant {t} has Event {name}[ with tag {tag}][ in funnel {s1, s2, ...}][ with property {prop}][ <value clause>].
Tenant {t} has Profile Property {name}[ with tag {tag}] <value clause>.
Tenant {t} has Billing Property {name}[ with tag {tag}] <value clause>.
```

`<value clause>` ∈
- `containing values like {v1, v2, ...}` (≤ first 5 truthy elements of `top_values`)
- `with mean {mean:.2f} and standard deviation {stddev:.2f}`
- `with date range from {date_min} to {date_max}`

---

## 11. Configuration (BigQuery / Weaviate / OpenAI)

Examples expect a `config.json` with this shape (canonical from `examples/config.json`):

```json
{
  "bigquery": {
    "service_account_path": "./service_account.json",
    "project_id": "...",
    "dataset": "...",
    "table": "tenant_metadata",
    "tenant_slug": "..."
  },
  "weaviate": {
    "http_host": "...",
    "http_port": 443,
    "grpc_host": "...",
    "grpc_port": 50051,
    "api_key": "...",
    "http_secure": true,
    "grpc_secure": false,
    "collection_name": "TenantMetadata_v3"
  },
  "openai": {
    "api_key": "..."
  }
}
```

Backward-compat keys to honor when reading config:
- `weaviate.host` (fallback if `http_host` missing)
- `weaviate.secure` (fallback for both `http_secure` and `grpc_secure`)
- env vars: `OPENAI_API_KEY`, `WEAVIATE_API_KEY` (override / fallback if config missing)

> **Security**: `config.json` is gitignored and must never contain real credentials in version control. Ship only a redacted template; supply real keys via environment variables (`OPENAI_API_KEY`, `WEAVIATE_API_KEY`) or an untracked local `config.json`.

### Weaviate collection schema (BigQuery path with `text2vec-openai`)

```python
properties = [
    Property(name="statement",         data_type=DataType.TEXT),
    Property(name="tenant_id",         data_type=DataType.TEXT),
    Property(name="source",            data_type=DataType.TEXT),
    Property(name="has_funnel_stage",  data_type=DataType.BOOL),
]
vectorizer_config = Configure.Vectorizer.text2vec_openai(model="text-embedding-3-small")
# headers={"X-OpenAI-Api-Key": <key>} on the client
```

Hybrid query defaults: `alpha=0.5`, `limit=10–20`, `return_metadata=MetadataQuery(score=True, explain_score=True)`. Filters built with `Filter.by_property("<k>").equal(<v>)` AND-combined.

---

## 12. Pipelines (end-to-end usage)

### Pipeline A — Postgres → Weaviate (manual SBERT vectors)

```python
scanner = PostgresSchemaScanner(sql_repo, schema="public", max_values=10, exclude_columns=[...])
transformer = SchemaTransformer(database="analytics", schema="public")
loader = WeaviateLoader(weaviate_host=..., http_port=..., grpc_port=..., api_key=...)

ss = SemanticSearch(sql_repo, scanner, transformer, loader,
                    schema_name="public",
                    weaviate_collection_name="SchemaStatements")

await ss.process_schema()                         # scan + load
results = await ss.semantic_search("show me ...", limit=5)
fixed_request, conf = await ss.semantic_search_schema_fix(user_request)
```

### Pipeline B — BigQuery → Weaviate (text2vec-openai)

```python
bq_repo = BigQueryReadRepository(bq_client)
scanner = BigQueryMetadataScanner(bq_repo, table_id="proj.ds.tenant_metadata")
transformer = SchemaTransformer(database="", schema="")

rows = scanner.get_metadata_for_tenant(tenant_slug, use_distinct=True)
stmts = await transformer.prepare_statements_from_bigquery(tenant_slug, rows)

# dedupe by statement text (dicts aren't hashable):
seen, unique = set(), []
for d in stmts:
    if d["statement"] not in seen:
        seen.add(d["statement"]); unique.append(d)

# load with text2vec-openai (manual; not via WeaviateLoader because of OpenAI headers)
client = weaviate.use_async_with_custom(... headers={"X-OpenAI-Api-Key": key} ...)
await client.connect()
if await client.collections.exists(name): await client.collections.delete(name)
collection = await client.collections.create(
    name=name, description=...,
    properties=[Property("statement", DataType.TEXT),
                Property("tenant_id", DataType.TEXT),
                Property("source", DataType.TEXT),
                Property("has_funnel_stage", DataType.BOOL)],
    vectorizer_config=Configure.Vectorizer.text2vec_openai(model="text-embedding-3-small"),
)
await collection.data.insert_many(unique)

# query
res = await collection.query.hybrid(query=q, alpha=0.5, limit=10,
                                    filters=Filter.by_property("tenant_id").equal(t)
                                          & Filter.by_property("source").equal("event"),
                                    return_metadata=MetadataQuery(score=True))
```

> **Recommendation for the rewrite**: extend `WeaviateLoader` to support a `vectorizer="openai" | "none"` mode that internally configures `Configure.Vectorizer.text2vec_openai(...)` and the `X-OpenAI-Api-Key` header. Then both pipelines can share the loader; current code duplicates the connect/create logic in `examples/bigquery_example_v2.py`.

---

## 13. Behavior to preserve verbatim (don't "improve" without grepping callers first)

1. **Statement strings** in §7.1 — round-trip with the §7.3 regexes; any whitespace / punctuation drift breaks the schema-fix flow.
2. **`MATCH_THRESHOLD = 70.0`** in `SemanticSearch.find_similar_values`.
3. **`%.2f` formatting** for numeric mean/stddev in both Postgres and BigQuery templates.
4. **Top-5 cap** on `top_values` in BigQuery story generation.
5. **High/extreme cardinality skip** — applies only to **profile** and **billing**, NOT to events.
6. **Profile/billing require a value clause; events do not.**
7. **`has_funnel_stage` key omitted (not False) when no funnel stages** — downstream filter `equal(False)` semantics depend on this.
8. **`prepare_statements_from_request` numeric template asymmetry** (`…and standard deviation.` vs `…standard deviation {std:.2f}.`) — see §7.2; investigate before normalizing.
9. **`SentenceTransformer` model**: `all-MiniLM-L6-v2` (384-dim). Changing requires reindexing the entire collection.
10. **Connection lifecycle**: `SemanticSearch.semantic_search` opens & closes the Weaviate client per call.

---

## 14. Things the rewrite SHOULD fix / clean up

| Issue | Where | Suggested fix |
|---|---|---|
| `WeaviateLoader.create_collection`'s `use_vectorizer` arg is unused | `weaviate_loader.py` | Either implement (switch to text2vec-openai) or drop the arg |
| BigQuery path bypasses `WeaviateLoader` | `examples/bigquery_example_v2.py` | Extend loader to support OpenAI headers + vectorizer config |
| Connect-per-query in `SemanticSearch.semantic_search` | `semantic_search.py` | Provide an `async with` context manager |
| Credentials in `config.json` | repo | Keep `config.json` gitignored; supply keys via env vars only |
| f-string SQL building (Postgres path) | `schema_scanner.py`, `_fetch_all_distinct_values` | Add identifier whitelist / quoting helper; document trust assumption |
| `prepare_statements_from_request` numeric template incomplete | `schema_transformer.py` | Either fix to match §7.1 or document as intentional |

These are **suggestions** — the user should sign off before any are applied during the rewrite.

---

## 15. Test surfaces (what to cover when rewriting)

- `utils.infer_value_type` — numeric/bool/date/text classification, with the order-of-checks edge case (`"1.0"` is numeric, not a date).
- `simplify_date` — datetime, date, and unparseable inputs.
- `find_best_match` — tie behavior (first equal-best wins via `>` not `>=`), empty candidates.
- `SchemaTransformer` round-trip: `prepare_statements_from_database` → `prepare_request_from_statements` recovers tables, columns, values, mean/std, date range, schema.
- BigQuery story generation: per-source coverage, cardinality skip, top-5 truncation, funnel-stage flag presence/absence, profile/billing emit-only-with-value rule.
- `WeaviateLoader.insert_data` accepts both `list[str]` and `list[dict]`; raises if no collection.
- `SemanticSearch.find_similar_values` — pair-by-index logic, threshold escalation, fallback to `_fetch_all_distinct_values`.

---

## 16. Migration checklist (when actually rewriting)

- [ ] Create `src/shared/semantic_search/` with the six modules from §2.
- [ ] Replace every `from semantic_search...` import with the new path.
- [ ] Update `__init__.py` re-exports.
- [ ] Delete `setup.py`, `setup.cfg`, top-level `semantic_search/` package dir, and `*.egg-info` once consumers are migrated.
- [ ] Move `examples/` to wherever the consuming project keeps demos, OR delete (they're tied to the old package layout).
- [ ] Confirm consumers updated — grep the codebase for `semantic_search` and `from semantic_search` before deleting.
- [ ] Add tests per §15.
- [ ] Confirm no credentials are committed; keep `config.json` gitignored and use env vars.
