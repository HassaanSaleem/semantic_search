from sql_repository import AsyncReadRepository
from typing import Dict, List, Optional, Set, Any
import logging


Logger = logging.getLogger(__name__)


class PostgresSchemaScanner:
    """
    Asynchronously scans PostgreSQL schema metadata using AsyncReadRepository.

    - For text columns: returns up to max_values distinct non-null values.
    - For numeric columns: returns mean and standard deviation.
    - For boolean columns: returns distinct boolean values.
    - For date/datetime columns: returns minimum and maximum values.
    - Skips columns that are in the exclude_columns list.
    """

    def __init__(
        self,
        sql_repository: AsyncReadRepository,
        schema: str = "public",
        max_values: int = 10,
        exclude_columns: Optional[List[str]] = None
    ) -> None:
        """
        :param sql_repository: AsyncReadRepository instance for DB queries.
        :param schema: The PostgreSQL schema name to inspect.
        :param max_values: Maximum distinct text values to collect.
        :param exclude_columns: Optional list of column names to skip.
        """
        self._sql_repository = sql_repository
        self.schema = schema
        self.max_values = max_values
        self.exclude_columns: Set[str] = set(exclude_columns) if exclude_columns else set()

        # Recognized PostgreSQL data types (in lowercase)
        self.text_data_types = {"character varying", "text", "varchar"}
        self.numeric_data_types = {"integer", "bigint", "smallint", "decimal", "numeric", "real", "double precision"}
        self.boolean_data_types = {"boolean"}
        self.date_data_types = {
            "date", "timestamp", "timestamp without time zone", "timestamp with time zone",
            "time", "time without time zone", "time with time zone"
        }

        # Union of all supported data types
        self.supported_types = (
            self.text_data_types |
            self.numeric_data_types |
            self.boolean_data_types |
            self.date_data_types
        )

    async def list_tables(self) -> List[str]:
        """
        Lists all base table names in the given schema.
        """
        query = f"""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = '{self.schema}'
                AND table_type = 'BASE TABLE';
        """
        try:
            results = await self._sql_repository.execute(query)
            return [row[0] for row in results]
        except Exception as e:
            Logger.error(f"Error executing list_tables: {e}", exc_info=True)
            return []

    async def list_columns_and_types(self, table_name: str) -> List[tuple]:
        """
        Returns a list of (column_name, data_type) for the given table,
        but only if the data_type is one of the supported types
        and the column is not in the exclude list.
        """
        query = f"""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = '{self.schema}'
                AND table_name = '{table_name}';
        """
        try:
            results = await self._sql_repository.execute(query)
            filtered = []
            for column_name, data_type in results:
                if column_name in self.exclude_columns:
                    continue
                dtype_lower = data_type.lower()
                if dtype_lower in self.supported_types:
                    filtered.append((column_name, dtype_lower))
            return filtered
        except Exception as e:
            Logger.error(f"Error executing list_columns_and_types: {e}", exc_info=True)
            return []

    async def fetch_text_distinct_values(
            self, table_name: str, column_name: str) -> List[str]:
        """
        For text columns: fetch up to max_values distinct non-null values.
        """
        query = f"""
            SELECT DISTINCT "{column_name}"
            FROM "{self.schema}"."{table_name}"
            WHERE "{column_name}" IS NOT NULL
            LIMIT {self.max_values};
        """
        try:
            results = await self._sql_repository.execute(query)
            return [str(row[0]) for row in results if row[0] is not None]
        except Exception as e:
            Logger.error(f"Error executing fetch_text_distinct_values: {e}", exc_info=True)
            return []

    async def fetch_numeric_stats(
            self, table_name: str, column_name: str) -> Dict[str, float]:
        """
        For numeric columns: fetch the mean and standard deviation.
        """
        query = f"""
            SELECT
                AVG("{column_name}") as avg_val,
                STDDEV_POP("{column_name}") as std_val
            FROM "{self.schema}"."{table_name}"
            WHERE "{column_name}" IS NOT NULL;
        """
        try:
            results = await self._sql_repository.execute(query)
            if not results or not results[0]:
                return {"mean": 0.0, "std": 0.0}
            mean_val, std_val = results[0]
            return {
                "mean": float(mean_val) if mean_val is not None else 0.0,
                "std": float(std_val) if std_val is not None else 0.0,
            }
        except Exception as e:
            Logger.error(f"Error executing fetch_numeric_stats: {e}", exc_info=True)
            return {"mean": 0.0, "std": 0.0}

    async def fetch_boolean_distinct_values(
            self, table_name: str, column_name: str) -> List[str]:
        """
        For boolean columns: fetch distinct non-null values.
        """
        query = f"""
            SELECT DISTINCT "{column_name}"
            FROM "{self.schema}"."{table_name}"
            WHERE "{column_name}" IS NOT NULL;
        """
        try:
            results = await self._sql_repository.execute(query)
            return [str(row[0]) for row in results if row[0] is not None]
        except Exception as e:
            Logger.error(f"Error executing fetch_boolean_distinct_values: {e}", exc_info=True)
            return []

    async def fetch_date_stats(
            self, table_name: str, column_name: str) -> Dict[str, Optional[str]]:
        """
        For date/datetime columns: fetch the minimum and maximum values.
        Returned values are cast to strings.
        """
        query = f"""
            SELECT
                MIN("{column_name}") as min_val,
                MAX("{column_name}") as max_val
            FROM "{self.schema}"."{table_name}"
            WHERE "{column_name}" IS NOT NULL;
        """
        try:
            results = await self._sql_repository.execute(query)
            if not results or not results[0]:
                return {"min": None, "max": None}
            min_val, max_val = results[0]
            return {
                "min": str(min_val) if min_val is not None else None,
                "max": str(max_val) if max_val is not None else None,
            }
        except Exception as e:
            Logger.error(f"Error executing fetch_date_stats: {e}", exc_info=True)
            return {"min": None, "max": None}

    async def get_table_column_data(
            self, table_name: str) -> Dict[str, Dict[str, Any]]:
        """
        Returns a dictionary describing columns and their aggregated data:
            - For text columns: { "type": "text", "values": [...] }
            - For numeric columns: { "type": "numeric", "mean": X, "std": Y }
            - For boolean columns: { "type": "boolean", "values": [...] }
            - For date/datetime columns: { "type": "date", "min": min_val, "max": max_val }
        """
        columns_info = await self.list_columns_and_types(table_name)
        column_data = {}

        for col_name, dtype in columns_info:
            if dtype in self.text_data_types:
                values = await self.fetch_text_distinct_values(table_name, col_name)
                if values:
                    column_data[col_name] = {
                        "type": "text",
                        "values": values
                    }
            elif dtype in self.numeric_data_types:
                stats = await self.fetch_numeric_stats(table_name, col_name)
                column_data[col_name] = {
                    "type": "numeric",
                    "mean": stats["mean"],
                    "std": stats["std"]
                }
            elif dtype in self.boolean_data_types:
                values = await self.fetch_boolean_distinct_values(table_name, col_name)
                if values:
                    column_data[col_name] = {
                        "type": "boolean",
                        "values": values
                    }
            elif dtype in self.date_data_types:
                stats = await self.fetch_date_stats(table_name, col_name)
                column_data[col_name] = {
                    "type": "date",
                    "min": stats["min"],
                    "max": stats["max"]
                }
        return column_data
