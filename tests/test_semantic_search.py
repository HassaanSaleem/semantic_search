from unittest.mock import AsyncMock, MagicMock
import json

from semantic_search import SemanticSearch


class FakeQueryVector:
    def __init__(self, vector):
        self._vector = vector

    def tolist(self):
        return self._vector


def make_collaborators():
    """Build a fully mocked (repo, scanner, transformer, loader) tuple."""
    repo = MagicMock()
    repo.execute = AsyncMock(return_value=[])

    scanner = MagicMock()
    scanner.list_tables = AsyncMock(return_value=["subscriptions"])
    scanner.get_table_column_data = AsyncMock(return_value={
        "plan_name": {"type": "text", "values": ["basic", "premium"]},
    })

    transformer = MagicMock()
    transformer.prepare_statements_from_database = AsyncMock(
        return_value=["statement one", "statement two"]
    )

    loader = MagicMock()
    loader.connect = AsyncMock()
    loader.recreate_collection = AsyncMock()
    loader.create_collection = AsyncMock()
    loader.insert_data = AsyncMock(return_value="insert-response")
    loader.close = AsyncMock()
    return repo, scanner, transformer, loader


class TestProcessSchema:
    async def test_process_schema_orchestrates_collaborators(self, tmp_path):
        repo, scanner, transformer, loader = make_collaborators()
        output_path = tmp_path / "table_column_data.json"
        ss = SemanticSearch(repo, scanner, transformer, loader,
                            weaviate_collection_name="TestCollection",
                            output_path=str(output_path))

        await ss.process_schema()

        scanner.list_tables.assert_awaited_once()
        scanner.get_table_column_data.assert_awaited_once_with("subscriptions")
        transformer.prepare_statements_from_database.assert_awaited_once_with(
            "subscriptions", {"plan_name": {"type": "text", "values": ["basic", "premium"]}}
        )
        loader.connect.assert_awaited_once()
        loader.recreate_collection.assert_awaited_once()
        assert loader.recreate_collection.await_args.kwargs["name"] == "TestCollection"
        loader.insert_data.assert_awaited_once_with(["statement one", "statement two"])
        loader.close.assert_awaited_once()

    async def test_process_schema_writes_column_data_to_output_path(self, tmp_path):
        repo, scanner, transformer, loader = make_collaborators()
        output_path = tmp_path / "out.json"
        ss = SemanticSearch(repo, scanner, transformer, loader,
                            output_path=str(output_path))

        await ss.process_schema()

        assert output_path.exists()
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data == {
            "subscriptions": {
                "plan_name": {"type": "text", "values": ["basic", "premium"]},
            }
        }

    async def test_process_schema_creates_missing_directories(self, tmp_path):
        repo, scanner, transformer, loader = make_collaborators()
        output_path = tmp_path / "nested" / "dir" / "out.json"
        ss = SemanticSearch(repo, scanner, transformer, loader,
                            output_path=str(output_path))

        await ss.process_schema()

        assert output_path.exists()

    async def test_process_schema_method_param_overrides_constructor(self, tmp_path):
        repo, scanner, transformer, loader = make_collaborators()
        constructor_path = tmp_path / "constructor.json"
        override_path = tmp_path / "override" / "out.json"
        ss = SemanticSearch(repo, scanner, transformer, loader,
                            output_path=str(constructor_path))

        await ss.process_schema(output_path=str(override_path))

        assert override_path.exists()
        assert not constructor_path.exists()

    async def test_default_output_path_preserves_legacy_location(self):
        repo, scanner, transformer, loader = make_collaborators()
        ss = SemanticSearch(repo, scanner, transformer, loader)
        assert ss._output_path == "resources/table_column_data.json"


class TestSemanticSearchQuery:
    async def test_semantic_search_queries_near_vector(self):
        repo, scanner, transformer, loader = make_collaborators()

        loader._model = MagicMock()
        loader._model.encode.return_value = FakeQueryVector([0.5, 0.6])

        result_obj = MagicMock()
        result_obj.properties = {"statement": "matched statement"}
        result_obj.metadata.certainty = 0.9
        result_obj.metadata.distance = 0.1
        result_obj.metadata.score = 0.8

        query_response = MagicMock()
        query_response.objects = [result_obj]

        collection = MagicMock()
        collection.query.near_vector = AsyncMock(return_value=query_response)
        loader._collection = collection

        ss = SemanticSearch(repo, scanner, transformer, loader)
        results = await ss.semantic_search("find subscription plans", limit=3, certainty=0.7)

        loader.connect.assert_awaited_once()
        loader._model.encode.assert_called_once_with("find subscription plans")
        collection.query.near_vector.assert_awaited_once()
        call_kwargs = collection.query.near_vector.await_args.kwargs
        assert call_kwargs["near_vector"] == [0.5, 0.6]
        assert call_kwargs["limit"] == 3
        assert call_kwargs["certainty"] == 0.7

        assert results == [{
            "statement": "matched statement",
            "certainty": 0.9,
            "distance": 0.1,
            "score": 0.8,
        }]
        loader.close.assert_awaited_once()

    async def test_semantic_search_creates_collection_when_missing(self):
        repo, scanner, transformer, loader = make_collaborators()

        loader._model = MagicMock()
        loader._model.encode.return_value = FakeQueryVector([0.5])
        loader._collection = None

        collection = MagicMock()
        query_response = MagicMock()
        query_response.objects = []
        collection.query.near_vector = AsyncMock(return_value=query_response)

        async def create_collection(name, description=""):
            loader._collection = collection

        loader.create_collection = AsyncMock(side_effect=create_collection)

        ss = SemanticSearch(repo, scanner, transformer, loader,
                            weaviate_collection_name="MyCollection")
        results = await ss.semantic_search("query")

        loader.create_collection.assert_awaited_once()
        assert loader.create_collection.await_args.kwargs["name"] == "MyCollection"
        assert results == []


class TestFindSimilarValues:
    async def test_matches_user_values_against_schema_values(self):
        repo, scanner, transformer, loader = make_collaborators()
        ss = SemanticSearch(repo, scanner, transformer, loader)

        request = {"parameters": {"plan_name": "premum"}}
        semantic_schema = {
            "parameters": {
                "plan_name": {"table": "subscriptions", "values": ["basic", "premium"]},
            }
        }

        result = await ss.find_similar_values(request, semantic_schema)

        col_info = result["parameters"]["plan_name"]
        assert col_info["request_value"] == "premum"
        assert col_info["similarity_match"] == "premium"
        assert col_info["similarity_score"] > 70.0
        # Confident match: no fallback DB query needed.
        repo.execute.assert_not_awaited()

    async def test_low_confidence_match_falls_back_to_db(self):
        repo, scanner, transformer, loader = make_collaborators()
        repo.execute = AsyncMock(return_value=[("enterprise",)])
        ss = SemanticSearch(repo, scanner, transformer, loader, schema_name="public")

        request = {"parameters": {"plan_name": "enterprize"}}
        semantic_schema = {
            "parameters": {
                "plan_name": {"table": "subscriptions", "values": ["basic"]},
            }
        }

        result = await ss.find_similar_values(request, semantic_schema)

        repo.execute.assert_awaited_once()
        query = repo.execute.await_args.args[0]
        assert '"public"."subscriptions"' in query
        assert '"plan_name"' in query
        col_info = result["parameters"]["plan_name"]
        assert col_info["similarity_match"] == "enterprise"
