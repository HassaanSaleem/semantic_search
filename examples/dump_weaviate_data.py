"""
Quick script to dump all Weaviate objects with specific filters.
Shows ALL properties to help debug what's actually stored.

Usage:
    python dump_weaviate_data.py acme billing
    python dump_weaviate_data.py acme billing 50  # limit to 50 results
"""

import asyncio
import json
import os
import sys
import weaviate
from weaviate.classes.query import Filter


async def dump_data(tenant_id: str, source: str, limit: int = 20):
    """Dump all objects matching the filters and show ALL properties."""
    
    # Load configuration
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    weaviate_config = config['weaviate']
    openai_config = config.get('openai', {})
    
    # Get API keys
    weaviate_api_key = weaviate_config.get('api_key', '') or os.getenv('WEAVIATE_API_KEY', '')
    openai_api_key = openai_config.get('api_key', '') or os.getenv('OPENAI_API_KEY', '')
    
    # Determine security settings
    http_secure = weaviate_config.get('http_secure', weaviate_config.get('secure', False))
    grpc_secure = weaviate_config.get('grpc_secure', weaviate_config.get('secure', False))
    
    # Connect to Weaviate
    print(f"Connecting to Weaviate...")
    
    headers = {
        "X-OpenAI-Api-Key": openai_api_key,
    } if openai_api_key else {}
    
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
    
    # Build filters
    filters = Filter.by_property("tenant_id").equal(tenant_id) & Filter.by_property("source").equal(source)
    
    print(f"\n{'='*100}")
    print(f"Fetching objects: tenant_id={tenant_id}, source={source}, limit={limit}")
    print(f"{'='*100}\n")
    
    # Fetch objects
    results = await collection.query.fetch_objects(
        filters=filters,
        limit=limit
    )
    
    if not results.objects:
        print("❌ No objects found!")
        await client.close()
        return
    
    print(f"✓ Found {len(results.objects)} objects\n")
    
    # Show all unique property names first
    all_properties = set()
    for obj in results.objects:
        all_properties.update(obj.properties.keys())
    
    print(f"📋 All property names found: {sorted(all_properties)}\n")
    print(f"{'='*100}\n")
    
    # Dump each object
    for i, obj in enumerate(results.objects, 1):
        print(f"[{i}] Object ID: {obj.uuid}")
        print(f"{'─'*100}")
        
        # Print all properties
        for key, value in sorted(obj.properties.items()):
            # Truncate long values
            value_str = str(value)
            if len(value_str) > 200:
                value_str = value_str[:200] + "..."
            print(f"  {key:20s}: {value_str}")
        
        print()
    
    await client.close()


async def main():
    if len(sys.argv) < 3:
        print("Usage: python dump_weaviate_data.py <tenant_id> <source> [limit]")
        print("Example: python dump_weaviate_data.py acme billing 20")
        return
    
    tenant_id = sys.argv[1]
    source = sys.argv[2]
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    
    await dump_data(tenant_id, source, limit)


if __name__ == "__main__":
    asyncio.run(main())

