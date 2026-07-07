"""
Simple script to list all Weaviate collections.

Usage:
    python get_collections.py
"""

import asyncio
import json
import os
import weaviate
from weaviate.classes.init import AdditionalConfig, Timeout


async def main():
    # Load config
    with open('config.json', 'r') as f:
        config = json.load(f)

    weaviate_config = config['weaviate']
    weaviate_api_key = weaviate_config.get('api_key', '') or os.getenv('WEAVIATE_API_KEY', '')

    # Connect
    client = weaviate.use_async_with_custom(
        http_host=weaviate_config.get('http_host', '127.0.0.1'),
        http_port=weaviate_config['http_port'],
        http_secure=weaviate_config.get('http_secure', weaviate_config.get('secure', False)),
        grpc_host=weaviate_config.get('grpc_host'),
        grpc_port=weaviate_config['grpc_port'],
        grpc_secure=weaviate_config.get('grpc_secure', weaviate_config.get('secure', False)),
        additional_config=AdditionalConfig(timeout=Timeout(init=30, query=60, insert=300)),
        auth_credentials=weaviate.auth.AuthApiKey(api_key=weaviate_api_key) if weaviate_api_key else None
    )

    await client.connect()

    # Get all collections
    collections = await client.collections.list_all()

    print(f"\n{'='*80}")
    print(f"Found {len(collections)} collection(s) in Weaviate:")
    print(f"{'='*80}\n")

    for i, (name, collection_config) in enumerate(collections.items(), 1):
        collection = client.collections.get(name)

        # Get count
        try:
            aggregate = await collection.aggregate.over_all(total_count=True)
            count = aggregate.total_count
        except:
            count = "?"

        print(f"{i}. Collection: {name}")
        print(f"   {'─'*70}")
        print(f"   Objects: {count:,}" if isinstance(count, int) else f"   Objects: {count}")

        # Show description if available
        if hasattr(collection_config, 'description') and collection_config.description:
            print(f"   Description: {collection_config.description}")

        # Show properties/schema
        if hasattr(collection_config, 'properties'):
            print(f"   Properties:")
            for prop in collection_config.properties:
                prop_name = prop.name if hasattr(prop, 'name') else str(prop)
                prop_type = prop.data_type if hasattr(prop, 'data_type') else '?'
                print(f"      • {prop_name} ({prop_type})")

        # Show vectorizer config
        if hasattr(collection_config, 'vectorizer_config'):
            vectorizer = collection_config.vectorizer_config
            if vectorizer is None:
                print(f"   Vectorizer: None (manual vectors)")
            else:
                vectorizer_name = vectorizer.vectorizer if hasattr(vectorizer, 'vectorizer') else str(vectorizer)
                print(f"   Vectorizer: {vectorizer_name}")

        # Show vector index config
        if hasattr(collection_config, 'vector_index_config'):
            vec_index = collection_config.vector_index_config
            if vec_index:
                distance = getattr(vec_index, 'distance_metric', '?')
                print(f"   Distance Metric: {distance}")

        print()

    print(f"{'='*80}\n")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
