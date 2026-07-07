"""
Semantic Search Query Tool (for text2vec-openai collections)

A simple tool to query an existing Weaviate collection with semantic search.
This version works with collections that use text2vec-openai vectorizer.
Enter a query and get back relevant statements with their certainty scores.

Prerequisites:
- Weaviate collection already created and populated (run bigquery_example_v2.py first)
- Weaviate running with text2vec-openai module enabled
- OpenAI API key configured in config.json

Configuration:
- Edit config.json with your Weaviate and OpenAI settings
- Set collection_name to the collection you want to query (e.g., "TenantMetadata_v2")

Usage:
    # One-liner with tenant_id, source, and query:
    python semantic_search_query_v2.py acme profile "email properties"
    python semantic_search_query_v2.py acme event "login events"
    python semantic_search_query_v2.py acme billing "subscription data"

    # One-liner with tenant_id, source, has_funnel_stage, and query:
    python semantic_search_query_v2.py acme event true "signup funnel"
    python semantic_search_query_v2.py acme event false "non-funnel events"
    python semantic_search_query_v2.py acme event null "all events"
"""

import asyncio
import json
import os
import sys
import weaviate
from weaviate.classes.query import MetadataQuery, Filter
from typing import Optional


# ============================================
# CONFIGURATION - Change these values here
# ============================================
DEFAULT_LIMIT = 10  # Maximum number of results to return
DEFAULT_ALPHA = 0.5  # 0.5 = balanced (50% semantic + 50% keyword)
# ============================================


async def semantic_search(
    query: str,
    collection,
    limit: int = 20,
    alpha: float = 0.5,
    tenant_id: Optional[str] = None,
    source: Optional[str] = None,
    has_funnel_stage: Optional[bool] = None
):
    """
    Perform hybrid search on the Weaviate collection (combines semantic + keyword search).

    :param query: Natural language search query
    :param collection: Weaviate collection instance
    :param limit: Maximum number of results to return
    :param alpha: Balance between semantic (1.0) and keyword (0.0) search. Default 0.5 = 50% semantic, 50% keyword
    :param tenant_id: Optional filter by tenant_id
    :param source: Optional filter by source ('event', 'profile', or 'billing')
    :param has_funnel_stage: Optional filter by has_funnel_stage (True for events with funnel stages, False otherwise)
    :return: List of results with statements and scores
    """
    # Build filters if provided
    filters = None
    filter_parts = []

    if tenant_id:
        filter_parts.append(Filter.by_property("tenant_id").equal(tenant_id))
    if source:
        filter_parts.append(Filter.by_property("source").equal(source))
    if has_funnel_stage is not None:
        filter_parts.append(Filter.by_property("has_funnel_stage").equal(has_funnel_stage))

    # Combine filters with AND logic
    if len(filter_parts) == 1:
        filters = filter_parts[0]
    elif len(filter_parts) > 1:
        filters = filter_parts[0]
        for fp in filter_parts[1:]:
            filters = filters & fp

    # Hybrid search: combines vector (semantic) + BM25 (keyword) search
    # With text2vec-openai, Weaviate automatically vectorizes the query
    results = await collection.query.hybrid(
        query=query,
        alpha=alpha,
        limit=limit,
        filters=filters,
        return_metadata=MetadataQuery(score=True, explain_score=True)
    )

    search_results = []
    for obj in results.objects:
        search_results.append({
            'statement': obj.properties.get('statement', ''),
            'tenant_id': obj.properties.get('tenant_id', ''),
            'source': obj.properties.get('source', ''),
            'has_funnel_stage': obj.properties.get('has_funnel_stage', False),
            'score': obj.metadata.score,
            'explain': obj.metadata.explain_score if hasattr(obj.metadata, 'explain_score') else None
        })

    return search_results


def print_results(query: str, results: list, alpha: float = 0.5, tenant_id: Optional[str] = None, source: Optional[str] = None, has_funnel_stage: Optional[bool] = None):
    """Pretty print search results."""
    print("\n" + "="*100)
    filter_info = ""
    if tenant_id or source or has_funnel_stage is not None:
        filters = []
        if tenant_id:
            filters.append(f"tenant_id={tenant_id}")
        if source:
            filters.append(f"source={source}")
        if has_funnel_stage is not None:
            filters.append(f"has_funnel_stage={has_funnel_stage}")
        filter_info = f" [Filters: {', '.join(filters)}]"
    print(f"🔍 Query: '{query}' (Hybrid: {alpha*100:.0f}% semantic + {(1-alpha)*100:.0f}% keyword){filter_info}")
    print("="*100)

    if not results:
        print("  ❌ No results found")
        return

    for i, result in enumerate(results, 1):
        statement = result['statement']
        result_tenant_id = result.get('tenant_id', '')
        result_source = result.get('source', '')
        result_has_funnel = result.get('has_funnel_stage', False)
        score = result.get('score')

        funnel_badge = "🎯" if result_has_funnel else ""
        print(f"\n[{i}] [{result_source}] {funnel_badge} {statement}")
        print(f"    📍 Tenant: {result_tenant_id}")
        if score is not None:
            print(f"    ✓ Score: {score:.4f}")


async def main():
    # Load configuration
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    weaviate_config = config['weaviate']
    openai_config = config.get('openai', {})
    
    # Get API keys from config or environment
    weaviate_api_key = weaviate_config.get('api_key', '') or os.getenv('WEAVIATE_API_KEY', '')
    openai_api_key = openai_config.get('api_key', '') or os.getenv('OPENAI_API_KEY', '')
    
    if not openai_api_key:
        print("\n✗ Error: OpenAI API key not found!")
        print("  Please add it to config.json under 'openai.api_key' or set OPENAI_API_KEY environment variable")
        return
    
    # Determine security settings
    http_secure = weaviate_config.get('http_secure', weaviate_config.get('secure', False))
    grpc_secure = weaviate_config.get('grpc_secure', weaviate_config.get('secure', False))
    
    # Connect to Weaviate
    print("Connecting to Weaviate...")
    
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
    
    collection_name = weaviate_config.get('collection_name', 'TenantMetadata_v2')
    
    print(f"Getting collection: {collection_name}")
    collection = client.collections.get(collection_name)

    # One-liner mode only: tenant_id source has_funnel_stage query
    if len(sys.argv) == 5:
        # Format: tenant_id source has_funnel_stage query
        tenant_id = sys.argv[1]
        source = sys.argv[2]
        funnel_arg = sys.argv[3].lower()
        query = sys.argv[4]

        # Validate source
        if source not in ['event', 'profile', 'billing']:
            print(f"❌ Error: source must be 'event', 'profile', or 'billing', got '{source}'")
            print("\nUsage: python semantic_search_query_v2.py <tenant_id> <source> <has_funnel_stage> <query>")
            print("Example: python semantic_search_query_v2.py acme event true \"signup funnel\"")
            return

        # Parse has_funnel_stage
        has_funnel_stage = None
        if funnel_arg == 'true':
            has_funnel_stage = True
        elif funnel_arg == 'false':
            has_funnel_stage = False
        elif funnel_arg != 'null' and funnel_arg != 'none':
            print(f"❌ Error: has_funnel_stage must be 'true', 'false', 'null', or 'none', got '{funnel_arg}'")
            print("\nUsage: python semantic_search_query_v2.py <tenant_id> <source> <has_funnel_stage> <query>")
            print("Example: python semantic_search_query_v2.py acme event true \"signup funnel\"")
            print("Example: python semantic_search_query_v2.py acme event null \"all events\"")
            return

        results = await semantic_search(query, collection, limit=DEFAULT_LIMIT, alpha=DEFAULT_ALPHA, tenant_id=tenant_id, source=source, has_funnel_stage=has_funnel_stage)
        print_results(query, results, alpha=DEFAULT_ALPHA, tenant_id=tenant_id, source=source, has_funnel_stage=has_funnel_stage)
    elif len(sys.argv) == 4:
        # Format: tenant_id source query
        tenant_id = sys.argv[1]
        source = sys.argv[2]
        query = sys.argv[3]

        # Validate source
        if source not in ['event', 'profile', 'billing']:
            print(f"❌ Error: source must be 'event', 'profile', or 'billing', got '{source}'")
            print("\nUsage: python semantic_search_query_v2.py <tenant_id> <source> <query>")
            print("Example: python semantic_search_query_v2.py acme profile \"email properties\"")
            return

        results = await semantic_search(query, collection, limit=DEFAULT_LIMIT, alpha=DEFAULT_ALPHA, tenant_id=tenant_id, source=source)
        print_results(query, results, alpha=DEFAULT_ALPHA, tenant_id=tenant_id, source=source)
    else:
        print("❌ Error: Invalid arguments")
        print("\nUsage:")
        print("  python semantic_search_query_v2.py <tenant_id> <source> <query>")
        print("  python semantic_search_query_v2.py <tenant_id> <source> <has_funnel_stage> <query>")
        print("\nExamples:")
        print("  python semantic_search_query_v2.py acme event \"login events\"")
        print("  python semantic_search_query_v2.py acme event true \"signup funnel\"")
        print("  python semantic_search_query_v2.py acme event null \"all events\"")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())

