from semantic_search.src.utils import infer_value_type, find_best_match
from semantic_search.src.schema_transformer import SchemaTransformer
from semantic_search.src.schema_scanner import PostgresSchemaScanner
from semantic_search.src.weaviate_loader import WeaviateLoader
from sql_repository import AsyncReadRepository
from typing import List, Optional
import logging
import asyncio
import json
import os

Logger = logging.getLogger(__name__)


class SemanticSearch:
    def __init__(
        self,
        sql_read_repository: AsyncReadRepository,
        schema_scanner: PostgresSchemaScanner,
        schema_transformer: SchemaTransformer,
        weaviate_loader: WeaviateLoader,
        schema_name: str = 'public',
        weaviate_collection_name: str = 'SchemaStatements',
        output_path: str = 'resources/table_column_data.json'
    ):
        self._sql_read_repository = sql_read_repository
        self._schema_scanner = schema_scanner
        self._schema_transformer = schema_transformer
        self._weaviate_loader = weaviate_loader
        self._schema_name = schema_name
        self._weaviate_collection_name = weaviate_collection_name
        self._output_path = output_path

    async def gather_all_column_data(self, tables: List[str]):
        tasks = {table: asyncio.create_task(self._schema_scanner.get_table_column_data(table)) for table in tables}
        results = {}
        for table, task in tasks.items():
            results[table] = await task
        return results

    async def process_schema(self, output_path: Optional[str] = None):
        """
        Scan the schema, push the generated statements to Weaviate and dump the
        collected column data as JSON.

        :param output_path: Where to write the column data JSON. Defaults to the
                            path passed to the constructor. Parent directories
                            are created if missing.
        """
        tables = await self._schema_scanner.list_tables()
        Logger.info(f"Tables in schema '{self._schema_name}': {tables}")

        table_column_data = await self.gather_all_column_data(tables)

        all_statements = []
        for table, column_data in table_column_data.items():
            statements = await self._schema_transformer.prepare_statements_from_database(table, column_data)
            all_statements.extend(statements)

        await self.push_to_weaviate(all_statements)

        output_path = output_path if output_path is not None else self._output_path
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(table_column_data, f, indent=2)

    async def push_to_weaviate(self, all_statements: List[str]):
        await self._weaviate_loader.connect()
        await self._weaviate_loader.recreate_collection(name=self._weaviate_collection_name, description="Statements from PostgreSQL schema.")
        response = await self._weaviate_loader.insert_data(all_statements)
        Logger.info(f"Insert response: {response}")
        await self._weaviate_loader.close()

    async def semantic_search(self, query_text: str, limit: int = 5, certainty: int = 0.85) -> List[dict]:
        """
        Compute the query vector using SentenceTransformer and return the top 5 similar objects.
        If the collection does not exist, create it first.
        """
        from weaviate.classes.query import MetadataQuery

        await self._weaviate_loader.connect()
        if not self._weaviate_loader._collection:
            Logger.info("Collection not found. Creating collection...")
            await self._weaviate_loader.create_collection(
                name=self._weaviate_collection_name,
                description="Statements from PostgreSQL schema."
            )

        query_vector = self._weaviate_loader._model.encode(query_text).tolist()
        Logger.info("Querying results for vector based on: %s", query_text)

        # Provide the vector as a flat list of floats.
        query_obj = await self._weaviate_loader._collection.query.near_vector(
            near_vector=query_vector,
            certainty=certainty,
            limit=limit,
            return_metadata=MetadataQuery(certainty=True, distance=True)
        )
        response = query_obj.objects
        Logger.info(response)

        results = []
        for i, obj in enumerate(response):
            results.append({
                'statement': obj.properties.get('statement'),
                'certainty': obj.metadata.certainty,
                'distance': obj.metadata.distance,
                'score': obj.metadata.score
            })

        await self._weaviate_loader.close()
        return results

    async def semantic_search_schema_fix(self, request: dict) -> tuple:
        statements = await self._schema_transformer.prepare_statements_from_request(request)

        semantic_search_statements = []
        certainty = 0
        count = 0
        for statement in statements:
            semantic_search_statement = await self.semantic_search(statement, limit=1, certainty=0.7)
            certainty += semantic_search_statement[0]['certainty']
            semantic_search_statements.append(semantic_search_statement[0]['statement'])
            count += 1
        avg_certainty = certainty / count if count else 0.0

        semantic_search_schema = await self._schema_transformer.prepare_request_from_statements(semantic_search_statements)

        semantic_search_schema = await self.find_similar_values(request, semantic_search_schema)

        return semantic_search_schema, avg_certainty

    async def find_similar_values(self, request: dict, semantic_schema: dict) -> dict:
        """
        Matches each text input in 'request' to the corresponding text-based parameter in 'semantic_schema' by index.
        """

        user_params: dict = request.get("parameters", {})
        schema_params: dict = semantic_schema.get("parameters", {})
        if not schema_params:
            return semantic_schema

        user_text_values = []
        for k, v in user_params.items():
            inferred_type, transformed_val = infer_value_type(v)
            if inferred_type != 'numeric':
                user_text_values.append((k, v))

        schema_text_params = []
        for col_name, col_info in schema_params.items():
            if "values" in col_info and isinstance(col_info["values"], list):
                schema_text_params.append((col_name, col_info))

        MATCH_THRESHOLD = 70.0
        pair_count = min(len(user_text_values), len(schema_text_params))

        for i in range(pair_count):
            user_col_name, user_val = user_text_values[i]
            schema_col, col_info = schema_text_params[i]
            existing_vals = col_info["values"] or []

            if not existing_vals:
                continue

            best_match, best_score = find_best_match(user_val, existing_vals)
            if best_score < MATCH_THRESHOLD:
                Logger.info(
                    f"Best match for user text '{user_val}' was '{best_match}' @ {best_score:.2f}%. "
                    "Fetching all distinct values from DB..."
                )
                table_name = col_info["table"]
                new_vals = await self._fetch_all_distinct_values(table_name, schema_col)
                combined = list(set(existing_vals + new_vals))

                new_match, new_score = find_best_match(user_val, combined)
                if new_score > best_score:
                    best_match, best_score = new_match, new_score
                    col_info["values"] = combined

            col_info["request_value"] = str(user_val)
            col_info["similarity_match"] = best_match
            col_info["similarity_score"] = round(best_score, 2)

        return semantic_schema

    async def _fetch_all_distinct_values(self, table_name: str, column_name: str) -> list[str]:
        """
        Queries your DB for ALL distinct values in a given table column.
        """
        query = f"""
            SELECT DISTINCT "{column_name}"
            FROM "{self._schema_name}"."{table_name}"
            WHERE "{column_name}" IS NOT NULL;
        """
        try:
            results = await self._sql_read_repository.execute(query)
            distinct_vals = [str(row[0]) for row in results if row[0] is not None]
            return distinct_vals
        except Exception as e:
            Logger.error(f"Error fetching distinct values for {table_name}.{column_name}: {e}", exc_info=True)
            return []
