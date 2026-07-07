from unittest.mock import AsyncMock, MagicMock
import sys
import types

import pytest

from semantic_search import WeaviateLoader


class FakeEmbeddings:
    def __init__(self, vectors):
        self._vectors = vectors

    def tolist(self):
        return self._vectors


def make_fake_model(vectors):
    model = MagicMock()
    model.encode.return_value = FakeEmbeddings(vectors)
    return model


class TestLazyModel:
    def test_constructor_does_not_load_model(self):
        loader = WeaviateLoader()
        assert loader._WeaviateLoader__model is None

    def test_model_is_loaded_lazily_from_sentence_transformers(self, monkeypatch):
        fake_module = types.ModuleType("sentence_transformers")
        fake_model = MagicMock(name="model")
        fake_module.SentenceTransformer = MagicMock(return_value=fake_model)
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

        loader = WeaviateLoader(model_name="some-model")
        assert loader._model is fake_model
        fake_module.SentenceTransformer.assert_called_once_with("some-model")

        # Second access reuses the cached instance.
        assert loader._model is fake_model
        fake_module.SentenceTransformer.assert_called_once()


class TestInsertData:
    @pytest.fixture
    def loader(self):
        loader = WeaviateLoader()
        loader._model = make_fake_model([[0.1, 0.2], [0.3, 0.4]])
        collection = MagicMock()
        collection.data.insert_many = AsyncMock(return_value="insert-response")
        loader._collection = collection
        return loader

    async def test_insert_data_requires_collection(self):
        loader = WeaviateLoader()
        loader._model = make_fake_model([[0.1]])
        with pytest.raises(RuntimeError):
            await loader.insert_data(["a statement"])

    async def test_insert_strings_wraps_statements_and_vectors(self, loader):
        statements = ["first statement", "second statement"]
        response = await loader.insert_data(statements)

        assert response == "insert-response"
        loader._model.encode.assert_called_once_with(statements)

        loader._collection.data.insert_many.assert_awaited_once()
        (data_objects,) = loader._collection.data.insert_many.await_args.args
        assert len(data_objects) == 2
        assert data_objects[0].properties == {"statement": "first statement"}
        assert data_objects[0].vector == [0.1, 0.2]
        assert data_objects[1].properties == {"statement": "second statement"}
        assert data_objects[1].vector == [0.3, 0.4]

    async def test_insert_dicts_passes_all_properties(self, loader):
        statements = [
            {"statement": "first", "tenant_id": "acme", "source": "event"},
            {"statement": "second", "tenant_id": "acme", "source": "profile"},
        ]
        await loader.insert_data(statements)

        loader._model.encode.assert_called_once_with(["first", "second"])
        (data_objects,) = loader._collection.data.insert_many.await_args.args
        assert data_objects[0].properties == statements[0]
        assert data_objects[1].properties == statements[1]
        assert data_objects[1].vector == [0.3, 0.4]


class TestClose:
    async def test_close_resets_client_and_collection(self):
        loader = WeaviateLoader()
        client = MagicMock()
        client.close = AsyncMock()
        loader._client = client
        loader._collection = MagicMock()

        await loader.close()

        client.close.assert_awaited_once()
        assert loader._client is None
        assert loader._collection is None
