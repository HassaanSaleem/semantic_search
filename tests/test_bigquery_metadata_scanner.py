from unittest.mock import MagicMock, patch

import pytest

import semantic_search.src.bigquery_metadata_scanner as bq_module
from semantic_search import BigQueryMetadataScanner


class TestOptionalDependency:
    def test_constructor_raises_actionable_import_error_when_adapter_missing(self):
        if bq_module.BigQueryReadRepository is not None:
            pytest.skip("bigquery_repository is installed in this environment")
        with pytest.raises(ImportError, match="bigquery_repository"):
            BigQueryMetadataScanner(MagicMock(), table_id="proj.ds.tenant_metadata")

    def test_module_imports_without_adapter(self):
        # The module itself must import cleanly even when the optional
        # adapter is not installed; only instantiation may fail.
        assert hasattr(bq_module, "BigQueryMetadataScanner")


class TestGetMetadataForTenant:
    @pytest.fixture
    def scanner(self):
        repo = MagicMock()
        repo.select_all.return_value = [{"tenant_slug": "acme", "source": "event"}]
        with patch.object(bq_module, "BigQueryReadRepository", MagicMock()):
            scanner = BigQueryMetadataScanner(repo, table_id="proj.ds.tenant_metadata")
        return scanner

    def test_distinct_query_construction(self, scanner):
        rows = scanner.get_metadata_for_tenant("acme")

        assert rows == [{"tenant_slug": "acme", "source": "event"}]
        scanner._bq_read_repository.select_all.assert_called_once()
        query = scanner._bq_read_repository.select_all.call_args.args[0]
        kwargs = scanner._bq_read_repository.select_all.call_args.kwargs
        assert "SELECT DISTINCT" in query
        assert "`proj.ds.tenant_metadata`" in query
        assert "WHERE tenant_slug = @tenant_slug" in query
        assert kwargs["parameters"] == {"tenant_slug": "acme"}

    def test_non_distinct_query_orders_by_computed_at(self, scanner):
        scanner.get_metadata_for_tenant("acme", use_distinct=False)

        query = scanner._bq_read_repository.select_all.call_args.args[0]
        assert "SELECT *" in query
        assert "ORDER BY computed_at DESC" in query

    def test_repository_error_returns_empty_list(self, scanner):
        scanner._bq_read_repository.select_all.side_effect = RuntimeError("boom")
        assert scanner.get_metadata_for_tenant("acme") == []
