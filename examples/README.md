# BigQuery Metadata to Weaviate Semantic Search

This directory contains examples demonstrating the complete pipeline for transforming BigQuery metadata into searchable Weaviate stories using semantic search.

## Available Scripts

### 1. `bigquery_example_v2.py` - Main Pipeline (with OpenAI Vectorizer)
The primary script that loads BigQuery metadata and creates a searchable Weaviate collection using OpenAI's text2vec embeddings.

**Features:**
- Loads metadata from BigQuery `tenant_metadata` table
- Transforms metadata into natural language stories
- Creates Weaviate collection with text2vec-openai vectorizer
- Automatically generates embeddings using OpenAI's API
- Runs example semantic searches

### 2. `semantic_search_query_v2.py` - Interactive Search Tool
Query an existing Weaviate collection with semantic search capabilities.

**Features:**
- Interactive mode with configurable search parameters
- Hybrid search (combines semantic + keyword search)
- Filter by tenant_id and source (event, profile, billing)
- Adjustable alpha parameter for semantic/keyword balance
- Command-line mode for quick queries

### 3. `dump_weaviate_data.py` - Data Inspector
Dump and inspect Weaviate objects with specific filters.

**Features:**
- View all properties of stored objects
- Filter by tenant_id and source
- Useful for debugging and data verification

### 4. `get_collections.py` - Collection Lister
List all Weaviate collections with their configurations.

**Features:**
- Shows collection names, object counts, and schemas
- Displays vectorizer and distance metric configurations
- Useful for understanding your Weaviate instance

## Setup

### 1. Install Dependencies

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install semantic_search
cd /path/to/semantic_search
pip install -e .

# The BigQuery examples additionally need the (not yet published)
# bigquery_repository companion adapter, installed from source:
pip install /path/to/bigquery_repository google-cloud-bigquery
```

### 2. Configure Credentials

#### BigQuery Credentials
Copy your BigQuery service account JSON file to the examples directory:
```bash
cd examples
cp /path/to/your/service_account.json ./service_account.json
```

**Important**: The `service_account.json` file should be in the `examples` folder and is gitignored for security.

#### OpenAI API Key
Add your OpenAI API key to `config.json` or set as environment variable:
```bash
export OPENAI_API_KEY="your-openai-api-key"
```

#### Weaviate API Key (if using authenticated Weaviate)
```bash
export WEAVIATE_API_KEY="your-weaviate-api-key"
```

### 3. Configure Settings

Edit `config.json`:
```json
{
  "bigquery": {
    "service_account_path": "./service_account.json",
    "project_id": "your-project-id",
    "dataset": "your_dataset",
    "table": "tenant_metadata",
    "tenant_slug": "your_tenant_slug"
  },
  "weaviate": {
    "http_host": "your-weaviate-host.com",
    "http_port": 443,
    "grpc_host": "your-weaviate-grpc-host.com",
    "grpc_port": 50051,
    "api_key": "your-weaviate-api-key",
    "http_secure": true,
    "grpc_secure": false,
    "collection_name": "TenantMetadata_v2"
  },
  "openai": {
    "api_key": "your-openai-api-key"
  }
}
```

### 4. Start Weaviate (if using local)

For local development, start Weaviate with the text2vec-openai module:

```bash
docker run -d \
  -p 8080:8080 \
  -p 50051:50051 \
  -e ENABLE_MODULES='text2vec-openai' \
  -e OPENAI_APIKEY='your-openai-api-key' \
  --name weaviate \
  cr.weaviate.io/semitechnologies/weaviate:latest
```

## Usage

### Full Pipeline: Load Data and Search

```bash
cd examples
python bigquery_example_v2.py
```

This will:
1. Load configuration from `config.json`
2. Connect to BigQuery and scan metadata for the specified tenant
3. Transform metadata into natural language stories
4. Create/recreate Weaviate collection with text2vec-openai vectorizer
5. Insert stories into Weaviate (embeddings generated automatically)
6. Run example semantic searches

### Skip Data Loading (Search Only)

If you've already loaded data and just want to run searches:

1. Edit `bigquery_example_v2.py` and set `SKIP_DATA_LOADING = True` (line 60)
2. Run: `python bigquery_example_v2.py`

### Interactive Semantic Search

Query your Weaviate collection interactively:

```bash
# Interactive mode
python semantic_search_query_v2.py

# One-liner with query only
python semantic_search_query_v2.py "email properties"

# One-liner with filters
python semantic_search_query_v2.py acme profile "email properties"
python semantic_search_query_v2.py acme event "login events"
```

**Interactive mode commands:**
- `limit=N` - Change result limit (default: 20)
- `alpha=X` - Adjust semantic/keyword balance (0.0-1.0, default: 0.5)
  - `alpha=1.0` = 100% semantic (meaning-based)
  - `alpha=0.5` = 50% semantic + 50% keyword (balanced)
  - `alpha=0.0` = 100% keyword (exact match)
- `tenant_id=X` - Filter by tenant
- `source=X` - Filter by source (event, profile, or billing)
- `clear_filters` - Remove all filters
- `exit` or `quit` - Exit

### Inspect Weaviate Data

Dump objects from Weaviate to see what's stored:

```bash
# Dump 20 billing objects for acme tenant
python dump_weaviate_data.py acme billing

# Dump 50 event objects
python dump_weaviate_data.py acme event 50
```

### List Collections

View all collections in your Weaviate instance:

```bash
python get_collections.py
```

## Processing Multiple Tenants

To process multiple tenants, run the script multiple times with different `tenant_slug` values in `config.json`:

```bash
# Process tenant 1
# Edit config.json: "tenant_slug": "tenant_1"
python bigquery_example_v2.py

# Process tenant 2
# Edit config.json: "tenant_slug": "tenant_2"
python bigquery_example_v2.py
```

**Note**: Each run will **recreate** the collection by default. To preserve data across runs, modify the script to skip the collection deletion step (lines 221-223 in `bigquery_example_v2.py`).

## How It Works

### Pipeline Overview

1. **BigQuery Metadata Extraction**
   - Connects to BigQuery using service account credentials
   - Queries `tenant_metadata` table for a specific tenant
   - Retrieves metadata rows for events, profile properties, and billing properties

2. **Story Transformation**
   - Converts structured metadata into natural language statements
   - Deduplicates statements to avoid redundancy
   - Preserves source information (event, profile, billing)

3. **Weaviate Storage**
   - Creates collection with text2vec-openai vectorizer
   - Weaviate automatically generates embeddings using OpenAI's API
   - Stores statements with metadata (tenant_id, source)

4. **Semantic Search**
   - Hybrid search combines semantic similarity + keyword matching
   - Configurable alpha parameter balances the two approaches
   - Filters available for tenant_id and source

### Story Format Examples

The pipeline generates natural language statements from metadata:

**Events:**
```
Tenant acme has Event user_signup, described as User completed registration, tagged as conversion.
Tenant acme has Event user_signup in funnel stage awareness.
```

**Profile Properties:**
```
Tenant acme has Profile Property email_domain, described as User email domain, with score 0.85.
Tenant acme has Profile Property user_status, containing distinct values active.
```

**Billing Properties:**
```
Tenant acme has Billing Property invoice_amount, described as Monthly invoice amount, with score 0.92.
```

## Troubleshooting

### OpenAI API Key Error
```
✗ Error: OpenAI API key not found!
```
**Solution:** Add your OpenAI API key to `config.json` under `openai.api_key` or set the `OPENAI_API_KEY` environment variable.

### No Metadata Found
```
✗ No metadata found for tenant 'your_tenant'
```
**Solution:**
- Verify `tenant_slug` exists in your BigQuery table
- Check table name and dataset in `config.json`
- Ensure service account has read permissions

### BigQuery Authentication Error
**Solution:**
- Ensure `service_account.json` path is correct in `config.json`
- Verify service account has BigQuery Data Viewer role
- Check that the service account JSON file is valid

### Weaviate Connection Error
**Solution:**
- Verify `http_host`, `http_port`, `grpc_host`, and `grpc_port` in `config.json`
- Check `http_secure` and `grpc_secure` settings match your Weaviate instance
- Ensure `WEAVIATE_API_KEY` is set if using authenticated instance
- For local Weaviate: verify it's running with `docker ps`
- Ensure text2vec-openai module is enabled in Weaviate

### Collection Not Found
```
Error: Collection 'TenantMetadata_v2' does not exist
```
**Solution:** Run `bigquery_example_v2.py` first to create and populate the collection.

## Configuration Reference

### BigQuery Configuration
- `service_account_path`: Path to service account JSON file (relative to examples directory)
- `project_id`: GCP project ID
- `dataset`: BigQuery dataset name
- `table`: Table name (typically "tenant_metadata")
- `tenant_slug`: Single tenant to process

### Weaviate Configuration
- `http_host`: Weaviate HTTP server hostname
- `http_port`: HTTP port (443 for HTTPS, 8080 for local)
- `grpc_host`: Weaviate gRPC server hostname (optional, can be same as http_host)
- `grpc_port`: gRPC port (default: 50051)
- `http_secure`: Use HTTPS (true for staging/prod, false for local)
- `grpc_secure`: Use secure gRPC (true for staging/prod, false for local)
- `api_key`: Weaviate API key (if authentication is enabled)
- `collection_name`: Name of the Weaviate collection (default: "TenantMetadata_v2")

### OpenAI Configuration
- `api_key`: OpenAI API key for text2vec-openai vectorizer

## Advanced Usage

### Custom Vectorizer Models

The default configuration uses `text-embedding-3-small`. To use a different model, edit `bigquery_example_v2.py` line 236:

```python
vectorizer_config=Configure.Vectorizer.text2vec_openai(
    model="text-embedding-3-large",  # or "text-embedding-ada-002"
    # dimensions=1024  # optional: specify dimensions for text-embedding-3-large
)
```

### Preserving Data Across Runs

By default, `bigquery_example_v2.py` recreates the collection on each run. To preserve existing data:

1. Comment out lines 221-223 in `bigquery_example_v2.py`:
```python
# if await client.collections.exists(collection_name):
#     print(f"  • Deleting existing {collection_name} collection...")
#     await client.collections.delete(collection_name)
```

2. Modify line 227 to use `create_or_get`:
```python
collection = await client.collections.create_or_get(...)
```

### Adjusting Search Parameters

In `semantic_search_query_v2.py`, you can modify default search behavior:

- **Limit**: Change default result count (line 208)
- **Alpha**: Adjust semantic/keyword balance (line 209)
  - 1.0 = Pure semantic search (meaning-based)
  - 0.5 = Balanced hybrid search (default)
  - 0.0 = Pure keyword search (BM25)

## Additional Resources

- [Weaviate Documentation](https://weaviate.io/developers/weaviate)
- [text2vec-openai Module](https://weaviate.io/developers/weaviate/modules/retriever-vectorizer-modules/text2vec-openai)
- [OpenAI Embeddings Guide](https://platform.openai.com/docs/guides/embeddings)
- [BigQuery Python Client](https://cloud.google.com/python/docs/reference/bigquery/latest)
