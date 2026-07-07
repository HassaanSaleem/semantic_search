"""
BigQuery Metadata to Weaviate Stories Pipeline (with text2vec-openai vectorizer)

This example demonstrates the complete flow:
1. Load metadata from BigQuery tenant_metadata table for a specific tenant
2. Transform metadata into natural language stories
3. Load stories into Weaviate for semantic search (using text2vec-openai vectorizer)
4. Perform example semantic searches

Key difference from bigquery_example.py:
- Uses text2vec-openai vectorizer instead of manual embeddings
- Weaviate automatically generates embeddings using OpenAI's API
- No need to manually encode query vectors

Prerequisites:
- semantic_search installed: pip install -e /path/to/semantic_search
- bigquery_repository's service_account.json configured
- Weaviate running with text2vec-openai module enabled

Configuration:
- Edit config.json with your BigQuery, Weaviate, and OpenAI settings
- Add OpenAI API key under "openai.api_key" in config.json
- Set collection_name in weaviate config (e.g., "TenantMetadata_v2")
- Set tenant_slug to the tenant you want to process
- Set SKIP_DATA_LOADING = True in main() to skip data loading and just run semantic search

Usage:
    # Full pipeline (load data + search):
    python bigquery_example_v2.py

    # Skip data loading and just run semantic search (if data already loaded):
    # Edit the file and set SKIP_DATA_LOADING = True on line 57
    python bigquery_example_v2.py
"""

import asyncio
import json
import os
from google.cloud import bigquery
from google.oauth2 import service_account
from weaviate.classes.query import MetadataQuery
from weaviate.classes.config import Configure, Property, DataType
import weaviate

from bigquery_repository import BigQueryReadRepository
from semantic_search import (
    BigQueryMetadataScanner,
    SchemaTransformer
)


async def main():
    print("="*80)
    print("BigQuery Metadata to Weaviate Stories Pipeline (text2vec-openai)")
    print("="*80)

    # =========================================================================
    # Configuration: Set to True to skip data loading and just run search
    # =========================================================================
    SKIP_DATA_LOADING = False  # Set to True to skip steps 2-5 and jump to step 6

    # =========================================================================
    # STEP 1: Load Configuration
    # =========================================================================
    print("\n[1/6] Loading configuration...")
    with open('config.json', 'r') as f:
        config = json.load(f)

    bq_config = config['bigquery']
    weaviate_config = config['weaviate']
    openai_config = config.get('openai', {})

    print(f"  ✓ BigQuery: {bq_config['project_id']}.{bq_config['dataset']}.{bq_config['table']}")
    print(f"  ✓ Tenant: {bq_config['tenant_slug']}")
    weaviate_host = weaviate_config.get('http_host', weaviate_config.get('host', '127.0.0.1'))
    print(f"  ✓ Weaviate: {weaviate_host}:{weaviate_config['http_port']}")

    # Get OpenAI API key from config or environment variable
    openai_api_key = openai_config.get('api_key', '') or os.getenv('OPENAI_API_KEY', '')
    if not openai_api_key:
        print("\n✗ Error: OpenAI API key not found!")
        print("  Please add it to config.json under 'openai.api_key' or set OPENAI_API_KEY environment variable")
        return

    print(f"  ✓ OpenAI API key configured")

    if SKIP_DATA_LOADING:
        print("\n⏭️  Skipping data loading steps (2-5), jumping to semantic search...")
    else:
        # =========================================================================
        # STEP 2: Initialize BigQuery Components
        # =========================================================================
        print("\n[2/6] Initializing BigQuery connection...")

        # Load BigQuery credentials from bigquery_repository
        credentials = service_account.Credentials.from_service_account_file(
            bq_config['service_account_path']
        )

        # Create BigQuery client
        bq_client = bigquery.Client(
            credentials=credentials,
            project=bq_config['project_id']
        )

        # Create read repository
        bq_read_repo = BigQueryReadRepository(bq_client)

        # Create metadata scanner
        table_id = f"{bq_config['project_id']}.{bq_config['dataset']}.{bq_config['table']}"
        scanner = BigQueryMetadataScanner(
            bq_read_repository=bq_read_repo,
            table_id=table_id
        )

        print(f"  ✓ Connected to BigQuery")
        print(f"  ✓ Scanner initialized for table: {table_id}")

        # =========================================================================
        # STEP 3: Scan Metadata from BigQuery
        # =========================================================================
        print(f"\n[3/6] Scanning metadata for tenant '{bq_config['tenant_slug']}'...")

        metadata_rows = scanner.get_metadata_for_tenant(bq_config['tenant_slug'])

        if not metadata_rows:
            print(f"  ✗ No metadata found for tenant '{bq_config['tenant_slug']}'")
            print("  Please check your tenant_slug in config.json")
            return

        print(f"  ✓ Retrieved {len(metadata_rows)} metadata rows")

        # Show breakdown by source
        sources = {}
        for row in metadata_rows:
            source = row.get('source', 'unknown')
            sources[source] = sources.get(source, 0) + 1

        for source, count in sources.items():
            print(f"    - {source}: {count} rows")

        # =========================================================================
        # STEP 4: Transform to Stories
        # =========================================================================
        print("\n[4/6] Transforming metadata to natural language stories...")

        transformer = SchemaTransformer(database="", schema="")

        statements = await transformer.prepare_statements_from_bigquery(
            bq_config['tenant_slug'],
            metadata_rows
        )

        # Deduplicate statements to avoid inserting duplicates into Weaviate
        original_count = len(statements)
        # Deduplicate based on statement text (since dicts are not hashable)
        seen_statements = set()
        unique_statements = []
        for stmt_dict in statements:
            stmt_text = stmt_dict['statement']
            if stmt_text not in seen_statements:
                seen_statements.add(stmt_text)
                unique_statements.append(stmt_dict)
        statements = unique_statements
        deduplicated_count = len(statements)

        print(f"  ✓ Generated {original_count} stories")
        if original_count != deduplicated_count:
            print(f"  ✓ Removed {original_count - deduplicated_count} duplicate statements")
            print(f"  ✓ Final unique stories: {deduplicated_count}")

        # Display sample stories
        print("\n  Sample stories:")
        for i, stmt_dict in enumerate(statements[:5], 1):
            print(f"    {i}. [{stmt_dict['source']}] {stmt_dict['statement']}")
        if len(statements) > 5:
            print(f"    ... and {len(statements) - 5} more")

        # =========================================================================
        # STEP 5: Load into Weaviate (with text2vec-openai vectorizer)
        # =========================================================================
        print("\n[5/6] Loading stories into Weaviate with text2vec-openai vectorizer...")

        # Get Weaviate API key (from config or environment)
        weaviate_api_key = weaviate_config.get('api_key', '') or os.getenv('WEAVIATE_API_KEY', '')

        # Check for security settings (support both old 'secure' and new 'http_secure'/'grpc_secure')
        http_secure = weaviate_config.get('http_secure', weaviate_config.get('secure', False))
        grpc_secure = weaviate_config.get('grpc_secure', weaviate_config.get('secure', False))

        if (http_secure or grpc_secure) and not weaviate_api_key:
            print("  ⚠ Warning: secure connection enabled but no API key set")

        # Connect to Weaviate
        print(f"  • Connecting to Weaviate...")

        headers = {
            "X-OpenAI-Api-Key": openai_api_key,
        }

        client = weaviate.use_async_with_custom(
            http_host=weaviate_config.get('http_host', weaviate_config.get('host', '127.0.0.1')),
            http_port=weaviate_config['http_port'],
            http_secure=http_secure,
            grpc_host=weaviate_config.get('grpc_host'),
            grpc_port=weaviate_config['grpc_port'],
            grpc_secure=grpc_secure,
            auth_credentials=weaviate.auth.AuthApiKey(api_key=weaviate_api_key) if weaviate_api_key else None,
            headers=headers
        )

        await client.connect()

        while not await client.is_ready():
            await asyncio.sleep(1)

        # Get collection name from config
        collection_name = weaviate_config.get('collection_name', 'TenantMetadata_v2')

        # Delete collection if it already exists (recreate)
        if await client.collections.exists(collection_name):
            print(f"  • Deleting existing {collection_name} collection...")
            await client.collections.delete(collection_name)

        # Create collection with text2vec-openai vectorizer
        print(f"  • Creating/recreating {collection_name} collection with text2vec-openai vectorizer...")
        collection = await client.collections.create(
            name=collection_name,
            description=f'Metadata stories from BigQuery for tenant {bq_config["tenant_slug"]}',
            properties=[
                Property(name="statement", data_type=DataType.TEXT),
                Property(name="tenant_id", data_type=DataType.TEXT),
                Property(name="source", data_type=DataType.TEXT),
                Property(name="has_funnel_stage", data_type=DataType.BOOL)  # New field to identify funnel events
            ],
            vectorizer_config=Configure.Vectorizer.text2vec_openai(
                model="text-embedding-3-small",  # Using text-embedding-3-small (default dimensions: 1536)
                # You can also use:
                # model="text-embedding-3-large", dimensions=1024
                # model="text-embedding-ada-002"
            )
        )

        print(f"  • Inserting {len(statements)} stories...")

        # Prepare data objects (no need to provide vectors - Weaviate will generate them)
        # statements is now a list of dicts with keys: 'statement', 'tenant_id', 'source'
        data_objects = statements

        # Batch insert
        response = await collection.data.insert_many(data_objects)

        # Check for errors
        if response.has_errors:
            print(f"  ⚠ Warning: {len(response.errors)} errors during insertion")
            for i, error in enumerate(response.errors[:5]):  # Show first 5 errors
                print(f"    Error {i+1}: {error}")
        else:
            print(f"  ✓ Successfully loaded {len(statements)} stories into Weaviate")

    # If skipping data loading, just connect to existing Weaviate collection
    if SKIP_DATA_LOADING:
        # Get Weaviate API key (from config or environment)
        weaviate_api_key = weaviate_config.get('api_key', '') or os.getenv('WEAVIATE_API_KEY', '')

        # Check for security settings
        http_secure = weaviate_config.get('http_secure', weaviate_config.get('secure', False))
        grpc_secure = weaviate_config.get('grpc_secure', weaviate_config.get('secure', False))

        # Connect to Weaviate
        print(f"  • Connecting to Weaviate...")

        headers = {
            "X-OpenAI-Api-Key": openai_api_key,
        }

        client = weaviate.use_async_with_custom(
            http_host=weaviate_config.get('http_host', weaviate_config.get('host', '127.0.0.1')),
            http_port=weaviate_config['http_port'],
            http_secure=http_secure,
            grpc_host=weaviate_config.get('grpc_host'),
            grpc_port=weaviate_config['grpc_port'],
            grpc_secure=grpc_secure,
            auth_credentials=weaviate.auth.AuthApiKey(api_key=weaviate_api_key) if weaviate_api_key else None,
            headers=headers
        )

        await client.connect()

        while not await client.is_ready():
            await asyncio.sleep(1)

        # Get collection name from config
        collection_name = weaviate_config.get('collection_name', 'TenantMetadata_v2')

        print(f"  • Getting existing {collection_name} collection...")
        collection = client.collections.get(collection_name)

    # =========================================================================
    # STEP 6: Semantic Search Examples
    # =========================================================================
    print("\n[6/6] Testing semantic search...")
    print("\n" + "="*80)
    print("SEMANTIC SEARCH EXAMPLES")
    print("="*80)

    # Example queries
    queries = [
        "Tenant acme activation event breakdown for paying customers: signup, user activated, download, install, login last 30 days paying only",
        "user signup events",
        "email properties",
        "revenue metrics",
        "profile properties with high scores"
    ]

    # Hybrid search: combines semantic (meaning) + keyword (exact match) search
    # alpha=0.5 means 50% semantic, 50% keyword (balanced)
    alpha = 0.5

    for query in queries:
        print(f"\n🔍 Query: '{query}' (Hybrid: {alpha*100:.0f}% semantic + {(1-alpha)*100:.0f}% keyword)")

        # Hybrid search combines vector similarity + BM25 keyword search
        # With text2vec-openai, Weaviate automatically vectorizes the query
        results = await collection.query.hybrid(
            query=query,
            alpha=alpha,
            limit=20,
            return_metadata=MetadataQuery(score=True)
        )

        if not results.objects:
            print("  (no results found)")
            continue

        for i, obj in enumerate(results.objects, 1):
            statement = obj.properties.get('statement', '')
            score = obj.metadata.score
            print(f"  [{i}] {statement}")
            if score is not None:
                print(f"      Score: {score:.3f}")
            else:
                print(f"      Score: N/A")

    await client.close()

    # =========================================================================
    # Summary
    # =========================================================================
    print("\n" + "="*80)
    print("✓ Pipeline completed successfully!")
    print("="*80)
    if not SKIP_DATA_LOADING:
        print(f"\nSummary:")
        print(f"  • Tenant: {bq_config['tenant_slug']}")
        print(f"  • Metadata rows: {len(metadata_rows)}")
        print(f"  • Stories generated: {len(statements)}")
        print(f"  • Weaviate collection: {collection_name}")
        print(f"  • Vectorizer: text2vec-openai (text-embedding-3-small)")
        print(f"\nYou can now query the collection using semantic search!")
    else:
        print(f"\nSummary:")
        print(f"  • Ran semantic search queries on existing collection: {collection_name}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()

