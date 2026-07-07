from semantic_search import PostgresSchemaScanner


class FakeRepository:
    """Records executed queries and returns canned results in order."""

    def __init__(self, results=None):
        self.queries = []
        self._results = list(results or [])

    async def execute(self, query):
        self.queries.append(query)
        if self._results:
            return self._results.pop(0)
        return []


class TestListTables:
    async def test_query_targets_configured_schema(self):
        repo = FakeRepository(results=[[("subscriptions",), ("invoices",)]])
        scanner = PostgresSchemaScanner(repo, schema="analytics")
        tables = await scanner.list_tables()

        assert tables == ["subscriptions", "invoices"]
        assert len(repo.queries) == 1
        query = repo.queries[0]
        assert "information_schema.tables" in query
        assert "table_schema = 'analytics'" in query
        assert "table_type = 'BASE TABLE'" in query

    async def test_repository_error_returns_empty_list(self):
        class FailingRepo:
            async def execute(self, query):
                raise RuntimeError("connection lost")

        scanner = PostgresSchemaScanner(FailingRepo())
        assert await scanner.list_tables() == []


class TestListColumnsAndTypes:
    async def test_filters_unsupported_types_and_excluded_columns(self):
        repo = FakeRepository(results=[[
            ("id", "uuid"),                     # unsupported type
            ("plan_name", "character varying"),
            ("amount", "numeric"),
            ("internal_notes", "text"),         # excluded
            ("created_at", "timestamp without time zone"),
        ]])
        scanner = PostgresSchemaScanner(repo, schema="public", exclude_columns=["internal_notes"])
        columns = await scanner.list_columns_and_types("subscriptions")

        assert columns == [
            ("plan_name", "character varying"),
            ("amount", "numeric"),
            ("created_at", "timestamp without time zone"),
        ]
        query = repo.queries[0]
        assert "information_schema.columns" in query
        assert "table_schema = 'public'" in query
        assert "table_name = 'subscriptions'" in query


class TestFetchQueries:
    async def test_text_distinct_values_query_respects_max_values(self):
        repo = FakeRepository(results=[[("basic",), ("premium",), (None,)]])
        scanner = PostgresSchemaScanner(repo, schema="public", max_values=7)
        values = await scanner.fetch_text_distinct_values("subscriptions", "plan_name")

        assert values == ["basic", "premium"]
        query = repo.queries[0]
        assert 'SELECT DISTINCT "plan_name"' in query
        assert '"public"."subscriptions"' in query
        assert '"plan_name" IS NOT NULL' in query
        assert "LIMIT 7" in query

    async def test_numeric_stats_query_and_result(self):
        repo = FakeRepository(results=[[(42.5, 3.25)]])
        scanner = PostgresSchemaScanner(repo)
        stats = await scanner.fetch_numeric_stats("subscriptions", "amount")

        assert stats == {"mean": 42.5, "std": 3.25}
        query = repo.queries[0]
        assert 'AVG("amount")' in query
        assert 'STDDEV_POP("amount")' in query
        assert '"amount" IS NOT NULL' in query

    async def test_numeric_stats_null_values_default_to_zero(self):
        repo = FakeRepository(results=[[(None, None)]])
        scanner = PostgresSchemaScanner(repo)
        stats = await scanner.fetch_numeric_stats("subscriptions", "amount")
        assert stats == {"mean": 0.0, "std": 0.0}

    async def test_date_stats_query_and_result(self):
        repo = FakeRepository(results=[[("2023-01-01", "2024-12-31")]])
        scanner = PostgresSchemaScanner(repo)
        stats = await scanner.fetch_date_stats("subscriptions", "created_at")

        assert stats == {"min": "2023-01-01", "max": "2024-12-31"}
        query = repo.queries[0]
        assert 'MIN("created_at")' in query
        assert 'MAX("created_at")' in query


class TestGetTableColumnData:
    async def test_aggregates_all_column_kinds(self):
        repo = FakeRepository(results=[
            # list_columns_and_types
            [
                ("plan_name", "text"),
                ("amount", "integer"),
                ("is_active", "boolean"),
                ("created_at", "date"),
            ],
            # fetch_text_distinct_values
            [("basic",), ("premium",)],
            # fetch_numeric_stats
            [(42.5, 3.25)],
            # fetch_boolean_distinct_values
            [(True,), (False,)],
            # fetch_date_stats
            [("2023-01-01", "2024-12-31")],
        ])
        scanner = PostgresSchemaScanner(repo)
        data = await scanner.get_table_column_data("subscriptions")

        assert data == {
            "plan_name": {"type": "text", "values": ["basic", "premium"]},
            "amount": {"type": "numeric", "mean": 42.5, "std": 3.25},
            "is_active": {"type": "boolean", "values": ["True", "False"]},
            "created_at": {"type": "date", "min": "2023-01-01", "max": "2024-12-31"},
        }
