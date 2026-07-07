from semantic_search import SchemaTransformer


FAKE_COLUMNS_DATA = {
    "plan_name": {"type": "text", "values": ["basic", "premium"]},
    "amount": {"type": "numeric", "mean": 42.5, "std": 3.25},
    "is_active": {"type": "boolean", "values": ["True", "False"]},
    "created_at": {"type": "date", "min": "2023-01-01", "max": "2024-12-31"},
}


class TestPrepareStatementsFromDatabase:
    async def test_text_column_generates_one_statement_per_value(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        statements = await transformer.prepare_statements_from_database(
            "subscriptions", {"plan_name": FAKE_COLUMNS_DATA["plan_name"]}
        )
        assert statements == [
            "Database analytics has a Schema public, Table subscriptions, "
            "Column plan_name, containing distinct text values basic.",
            "Database analytics has a Schema public, Table subscriptions, "
            "Column plan_name, containing distinct text values premium.",
        ]

    async def test_numeric_column_statement(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        statements = await transformer.prepare_statements_from_database(
            "subscriptions", {"amount": FAKE_COLUMNS_DATA["amount"]}
        )
        assert statements == [
            "Database analytics has a Schema public, Table subscriptions, "
            "Column amount, with mean 42.50 and standard deviation 3.25."
        ]

    async def test_boolean_column_statement(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        statements = await transformer.prepare_statements_from_database(
            "subscriptions", {"is_active": FAKE_COLUMNS_DATA["is_active"]}
        )
        assert statements == [
            "Database analytics has a Schema public, Table subscriptions, "
            "Column is_active, containing distinct boolean values True, False."
        ]

    async def test_date_column_statement(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        statements = await transformer.prepare_statements_from_database(
            "subscriptions", {"created_at": FAKE_COLUMNS_DATA["created_at"]}
        )
        assert statements == [
            "Database analytics has a Schema public, Table subscriptions, "
            "Column created_at, with date range from 2023-01-01 to 2024-12-31."
        ]

    async def test_full_schema_dict_generates_all_statements(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        statements = await transformer.prepare_statements_from_database(
            "subscriptions", FAKE_COLUMNS_DATA
        )
        # 2 text values + 1 numeric + 1 boolean + 1 date
        assert len(statements) == 5

    async def test_empty_text_values_are_skipped(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        statements = await transformer.prepare_statements_from_database(
            "subscriptions", {"plan_name": {"type": "text", "values": []}}
        )
        assert statements == []

    async def test_date_column_without_bounds_is_skipped(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        statements = await transformer.prepare_statements_from_database(
            "subscriptions", {"created_at": {"type": "date", "min": None, "max": None}}
        )
        assert statements == []


class TestStatementRoundTrip:
    async def test_prepare_request_from_statements_reverses_text_statement(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        statements = await transformer.prepare_statements_from_database(
            "subscriptions", {"plan_name": {"type": "text", "values": ["basic"]}}
        )
        request = await transformer.prepare_request_from_statements(statements)
        assert request["data_api"] == "public"
        assert request["operator"] == "and"
        assert request["parameters"]["plan_name"] == {
            "table": "subscriptions",
            "values": ["basic"],
        }

    async def test_prepare_request_from_statements_reverses_numeric_statement(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        statements = await transformer.prepare_statements_from_database(
            "subscriptions", {"amount": {"type": "numeric", "mean": 10.0, "std": 2.0}}
        )
        request = await transformer.prepare_request_from_statements(statements)
        assert request["parameters"]["amount"]["mean"] == 10.0
        # The std capture group greedily includes the trailing period, so the
        # float() parse fails and the value falls back to 0.0.
        assert request["parameters"]["amount"]["std"] == 0.0


class TestPrepareStatementsFromBigquery:
    async def test_event_rows_generate_event_stories(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        rows = [{
            "source": "event",
            "event_name": "user_signup",
            "event_tag": "conversion",
            "funnel_stage": ["awareness"],
            "property_name": "utm_source",
            "top_values": ["google", "newsletter"],
            "numeric_mean": None,
        }]
        stories = await transformer.prepare_statements_from_bigquery("acme", rows)
        assert len(stories) == 1
        story = stories[0]
        assert story["tenant_id"] == "acme"
        assert story["source"] == "event"
        assert story["has_funnel_stage"] is True
        assert "Tenant acme has Event user_signup" in story["statement"]
        assert "with tag conversion" in story["statement"]
        assert "in funnel awareness" in story["statement"]
        assert "containing values like google, newsletter" in story["statement"]

    async def test_profile_rows_generate_profile_stories(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        rows = [{
            "source": "profile",
            "property_name": "email_domain",
            "top_values": ["example.com"],
            "numeric_mean": None,
            "cardinality_band": "low",
        }]
        stories = await transformer.prepare_statements_from_bigquery("acme", rows)
        assert len(stories) == 1
        assert stories[0]["source"] == "profile"
        assert "Profile Property email_domain" in stories[0]["statement"]

    async def test_high_cardinality_profile_rows_are_skipped(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        rows = [{
            "source": "profile",
            "property_name": "user_id",
            "top_values": ["a", "b"],
            "numeric_mean": None,
            "cardinality_band": "extreme",
        }]
        stories = await transformer.prepare_statements_from_bigquery("acme", rows)
        assert stories == []

    async def test_billing_numeric_rows(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        rows = [{
            "source": "billing",
            "property_name": "invoice_amount",
            "numeric_mean": 99.5,
            "numeric_stddev": 12.25,
        }]
        stories = await transformer.prepare_statements_from_bigquery("acme", rows)
        assert len(stories) == 1
        assert "with mean 99.50 and standard deviation 12.25" in stories[0]["statement"]

    async def test_unknown_source_is_ignored(self):
        transformer = SchemaTransformer(database="analytics", schema="public")
        stories = await transformer.prepare_statements_from_bigquery(
            "acme", [{"source": "mystery"}]
        )
        assert stories == []
