from __future__ import annotations
from typing import List, Any, Union, Dict, Optional
import logging
import asyncio


Logger = logging.getLogger(__name__)


class WeaviateLoader:
    """
    Handles connecting to Weaviate, creating a schema (collection),
    and inserting the data (text) so Weaviate can vectorize it internally
    via text2vec-transformers (or another text2vec module).

    The heavy dependencies (`weaviate-client`, `sentence-transformers`) are
    imported lazily so that importing this module does not require them.
    The embedding model is only loaded on first use.
    """

    def __init__(
        self,
        weaviate_host: str = "127.0.0.1",
        http_port: int = 8080,
        grpc_port: int = 50051,
        api_key: str = '',
        secure: bool = False,
        grpc_host: str = None,
        http_secure: bool = None,
        grpc_secure: bool = None,
        model_name: str = "all-MiniLM-L6-v2"
    ) -> None:

        self._api_key = api_key
        self._host = weaviate_host
        self._grpc_host = grpc_host if grpc_host else weaviate_host
        self._http_port = http_port
        self._grpc_port = grpc_port
        # Allow separate control of HTTP and gRPC security, fallback to 'secure' for backward compatibility
        self._http_secure = http_secure if http_secure is not None else secure
        self._grpc_secure = grpc_secure if grpc_secure is not None else secure
        self._client = None
        self._collection = None
        self._model_name = model_name
        self.__model = None

    @property
    def _model(self):
        """Lazily load the SentenceTransformer model on first access."""
        if self.__model is None:
            from sentence_transformers import SentenceTransformer
            self.__model = SentenceTransformer(self._model_name)
        return self.__model

    @_model.setter
    def _model(self, value) -> None:
        self.__model = value

    async def connect(self) -> None:
        """Connect to the Weaviate server asynchronously."""
        import weaviate
        from weaviate.classes.init import AdditionalConfig, Timeout

        self._client = weaviate.use_async_with_custom(
            http_host=self._host,
            http_port=self._http_port,
            http_secure=self._http_secure,
            grpc_host=self._grpc_host,
            grpc_port=self._grpc_port,
            grpc_secure=self._grpc_secure,
            additional_config=AdditionalConfig(timeout=Timeout(init=30, query=60, insert=300)),
            auth_credentials=weaviate.auth.AuthApiKey(api_key=self._api_key) if self._api_key else None)
        await self._client.connect()

        while not await self._client.is_ready():
            await asyncio.sleep(1)

    async def create_collection(
        self,
        name: str,
        description: str = "",
        use_vectorizer: bool = False,
        properties: Optional[List[Any]] = None
    ) -> Any:
        """
        Create a Weaviate collection (class).

        :param name: Collection name
        :param description: Collection description
        :param use_vectorizer: If True, uses none vectorizer (manual vectors). If False, expects Weaviate to have text2vec module.
        :param properties: List of Property objects. If None, defaults to [Property(name="statement", data_type=DataType.TEXT)]
        """
        from weaviate.classes.config import Property, DataType

        # Default properties if none provided
        if properties is None:
            properties = [Property(name="statement", data_type=DataType.TEXT)]

        collection = self._client.collections.get(name=name)
        if not collection:
            collection = await self._client.collections.create(
                name=name,
                description=description,
                properties=properties,
                vectorizer_config=None
            )
        self._collection = collection
        return collection

    async def recreate_collection(
        self,
        name: str,
        description: str = "",
        properties: Optional[List[Any]] = None
    ):
        """
        Delete and recreate a Weaviate collection.

        :param name: Collection name
        :param description: Collection description
        :param properties: List of Property objects. If None, defaults to [Property(name="statement", data_type=DataType.TEXT)]
        """
        existing = self._client.collections.get(name=name)
        if existing:
            await self._client.collections.delete(name=name)

        return await self.create_collection(name, description, properties=properties)

    async def insert_data(self, statements: Union[List[str], List[Dict[str, Any]]]) -> Any:
        """
        Insert data with manually computed embeddings.

        :param statements: List of statements (strings) or list of dictionaries with properties.
                          If dict, must contain 'statement' key. Can also contain 'tenant_id', 'source', etc.
        :return: Insert response from Weaviate
        """
        from weaviate.classes.data import DataObject

        if not self._collection:
            raise RuntimeError("Collection not created or set. Call create_collection first.")

        # Extract statement text for embedding computation
        if statements and isinstance(statements[0], dict):
            # List of dictionaries
            statement_texts = [item['statement'] for item in statements]
        else:
            # List of strings (backward compatibility)
            statement_texts = statements

        # Compute embeddings using sentence-transformers
        embeddings = self._model.encode(statement_texts).tolist()

        # Prepare Weaviate objects with vectors
        data_objects = []
        for i, vector in enumerate(embeddings):
            if isinstance(statements[i], dict):
                # Use all properties from the dictionary
                properties = statements[i]
            else:
                # Backward compatibility: just statement
                properties = {"statement": statements[i]}

            data_objects.append(DataObject(properties=properties, vector=vector))

        response = await self._collection.data.insert_many(data_objects)
        return response

    async def close(self) -> None:
        """Close the connection to Weaviate."""
        if self._client:
            await self._client.close()
            self._client = None
        self._collection = None
