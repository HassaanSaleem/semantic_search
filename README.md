# Semantic Search

A reusable Python library for turning tabular metadata into searchable natural-language
statements backed by [Weaviate](https://weaviate.io) and
[sentence-transformers](https://www.sbert.net).

## What it does

1. **Scans tabular metadata** from either a PostgreSQL schema (live introspection) or a
   pre-aggregated BigQuery table.
2. **Transforms** that metadata into natural-language statements / stories using
   deterministic templates.
3. **Loads** those statements into a Weaviate collection — either with manual
   SentenceTransformer embeddings (`all-MiniLM-L6-v2`) or a Weaviate-managed vectorizer
   (e.g. `text2vec-openai`).
4. **Queries** the collection via near-vector or hybrid search, optionally filtered by
   `tenant_id`, `source`, and `has_funnel_stage`.
5. **Reverses** statements back into a structured request and fuzzy-matches user-supplied
   values against known categorical values from the underlying database.

See [`SPEC.md`](./SPEC.md) for the full module specification.

## Installation

```bash
pip install -e .

# With the test dependencies:
pip install -e ".[dev]"
```

All dependencies are declared in `pyproject.toml`; there is no separate
`requirements.txt`. The two required companion packages (`sql_repository`,
`events_repository`) are installed automatically as git dependencies.

## Companion packages

This library depends on a few companion packages that are published as **separate sibling
repositories** (they are not on PyPI):

| Package | Purpose | Required |
|---|---|---|
| `sql_repository` | Async PostgreSQL read repository (`AsyncReadRepository`) | Yes — installed automatically from GitHub |
| `events_repository` | Datetime validation helpers (`DatetimeValidator`) | Yes — installed automatically from GitHub |
| `bigquery_repository` | BigQuery read repository (`BigQueryReadRepository`) | Optional — only for `BigQueryMetadataScanner` |

**BigQuery scanner:** `BigQueryMetadataScanner` requires the `bigquery_repository`
companion adapter, which is not yet published. The rest of the library works without
it — instantiating the scanner without the adapter installed raises an `ImportError`
that explains what to install.

## Quick start

```python
from semantic_search import (
    PostgresSchemaScanner,
    SchemaTransformer,
    WeaviateLoader,
    SemanticSearch,
)

scanner = PostgresSchemaScanner(sql_repo, schema="public", max_values=10)
transformer = SchemaTransformer(database="analytics", schema="public")
loader = WeaviateLoader(weaviate_host="localhost", http_port=8080, grpc_port=50051)

ss = SemanticSearch(sql_repo, scanner, transformer, loader,
                    schema_name="public",
                    weaviate_collection_name="SchemaStatements")

await ss.process_schema()                       # scan + load
results = await ss.semantic_search("show me ...", limit=5)
```

See the [`examples/`](./examples) directory for runnable BigQuery and Weaviate scripts.

## License

MIT — see [`LICENSE.txt`](./LICENSE.txt).
