from __future__ import annotations
from typing import Dict, List, Any
import logging

try:
    # Optional dependency: companion package published as a separate sibling
    # repo, not yet on PyPI. Install it from source before using this scanner.
    from bigquery_repository import BigQueryReadRepository
except ImportError:  # pragma: no cover - optional dependency
    BigQueryReadRepository = None


Logger = logging.getLogger(__name__)


class BigQueryMetadataScanner:
    """
    Scans a pre-aggregated BigQuery metadata table to retrieve metadata for events, profile properties, and billing properties.

    Uses BigQueryReadRepository to execute parameterized queries and returns all metadata rows for a given tenant.
    """

    def __init__(self, bq_read_repository: "BigQueryReadRepository", table_id: str) -> None:
        """
        Initialize the BigQuery metadata scanner.

        :param bq_read_repository: BigQueryReadRepository instance for querying BigQuery
        :param table_id: Full table ID in format 'project.dataset.table_name'
        """
        if BigQueryReadRepository is None:
            raise ImportError(
                "BigQueryMetadataScanner requires the 'bigquery_repository' companion package, "
                "which is not yet published. Install it from source "
                "(pip install /path/to/bigquery_repository) together with "
                "google-cloud-bigquery>=3.35, then retry."
            )
        self._bq_read_repository = bq_read_repository
        self.table_id = table_id

    def get_metadata_for_tenant(self, tenant_slug: str, use_distinct: bool = True) -> List[Dict[str, Any]]:
        """
        Query all metadata rows for a specific tenant from BigQuery.

        Returns all rows including events, profile properties, and billing properties.
        The source column distinguishes between data types ('event', 'profile', 'billing').

        :param tenant_slug: Tenant identifier to filter metadata rows
        :param use_distinct: If True, uses DISTINCT to avoid duplicate rows (recommended)
        :return: List of row dictionaries with all fields. REPEATED fields come as Python lists.
        """
        # Use DISTINCT to get only unique metadata rows, excluding computed_at from comparison
        # This prevents duplicate rows from different computation times
        if use_distinct:
            query = f"""
                SELECT DISTINCT
                    tenant_slug,
                    source,
                    event_name,
                    event_description,
                    event_tag,
                    property_name,
                    property_description,
                    property_score,
                    property_tag,
                    top_values,
                    numeric_mean,
                    numeric_stddev,
                    date_min,
                    date_max,
                    cardinality_band,
                    funnel_stage,
                    metric,
                    correlation_value
                FROM `{self.table_id}`
                WHERE tenant_slug = @tenant_slug
            """
        else:
            query = f"""
                SELECT *
                FROM `{self.table_id}`
                WHERE tenant_slug = @tenant_slug
                ORDER BY computed_at DESC
            """

        try:
            results = self._bq_read_repository.select_all(
                query,
                parameters={"tenant_slug": tenant_slug}
            )
            Logger.info(f"Retrieved {len(results)} metadata rows for tenant '{tenant_slug}' (distinct={use_distinct})")
            return results
        except Exception as e:
            Logger.error(f"Error querying metadata for tenant '{tenant_slug}': {e}", exc_info=True)
            return []
