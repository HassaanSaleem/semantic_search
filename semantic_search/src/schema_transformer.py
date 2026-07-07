from semantic_search.src.utils import simplify_date, infer_value_type
from typing import Dict, List, Any
import logging
import re

Logger = logging.getLogger(__name__)


class SchemaTransformer:
    """
    Takes table/column info and aggregated data about each column (text distinct values, numeric stats,
    boolean distinct values, or date range) and prepares textual statements about them.
    """

    def __init__(self, database: str, schema: str) -> None:
        self.database = database
        self.schema = schema

    async def prepare_statements_from_database(self, table_name: str, columns_data: Dict[str, Dict[str, Any]]) -> List[str]:
        """
        Build statements of the form:
            For text columns:
                "Database {db} has a Schema {schema}, Table {table}, Column {col}, containing distinct text values val1"
                "Database {db} has a Schema {schema}, Table {table}, Column {col}, containing distinct text values val2"
                ...
            For numeric columns:
                "Database {db} has a Schema {schema}, Table {table}, Column {col}, with mean {X} and standard deviation {Y}."
            For boolean columns:
                "Database {db} has a Schema {schema}, Table {table}, Column {col}, containing distinct boolean values val1, val2."
            For date/datetime columns:
                "Database {db} has a Schema {schema}, Table {table}, Column {col}, with date range from {min} to {max}."
        """
        statements = []
        for col_name, meta in columns_data.items():
            col_type = meta["type"]
            if col_type == "text":
                values = meta["values"]
                if not values:
                    continue
                # Generate one statement per distinct text value
                for val in values:
                    statement = (
                        f"Database {self.database} has a Schema {self.schema}, Table {table_name}, "
                        f"Column {col_name}, containing distinct text values {val}."
                    )
                    statements.append(statement)

            elif col_type == "numeric":
                mean_val = meta["mean"]
                std_val = meta["std"]
                statement = (
                    f"Database {self.database} has a Schema {self.schema}, Table {table_name}, "
                    f"Column {col_name}, with mean {mean_val:.2f} and standard deviation {std_val:.2f}."
                )
                statements.append(statement)

            elif col_type == "boolean":
                values = meta["values"]
                if not values:
                    continue
                distinct_str = ", ".join(values)
                statement = (
                    f"Database {self.database} has a Schema {self.schema}, Table {table_name}, "
                    f"Column {col_name}, containing distinct boolean values {distinct_str}."
                )
                statements.append(statement)

            elif col_type == "date":
                min_val = meta.get("min")
                max_val = meta.get("max")
                if min_val is None and max_val is None:
                    continue
                statement = (
                    f"Database {self.database} has a Schema {self.schema}, Table {table_name}, "
                    f"Column {col_name}, with date range from {min_val} to {max_val}."
                )
                statements.append(statement)
        return statements

    async def prepare_statements_from_request(self, request: dict, database: str = "analytics", table_name: str = "") -> list:
        """
        Build statements of the form:
            For text columns:
                "Database {db} has a Schema {schema}, Table {table}, Column {col}, containing distinct text values val1"
                "Database {db} has a Schema {schema}, Table {table}, Column {col}, containing distinct text values val2"
                ...
            For numeric columns:
                "Database {db} has a Schema {schema}, Table {table}, Column {col}, with mean {X} and standard deviation {Y}."
            For boolean columns:
                "Database {db} has a Schema {schema}, Table {table}, Column {col}, containing distinct boolean values val1, val2."
            For date/datetime columns:
                "Database {db} has a Schema {schema}, Table {table}, Column {col}, with date range from {min} to {max}."
        """
        schema = request.get('data_api', 'public')
        parameters = request.get('parameters', {})

        statements = []
        for col, raw_val in parameters.items():
            inferred_type, value = infer_value_type(raw_val)

            if inferred_type == 'numeric':
                stmt = (
                    f"Database {database} has a Schema {schema}, Table {table_name}, "
                    f"Column {col}, with mean {value:.2f} and standard deviation."
                )
            elif inferred_type == 'boolean':
                stmt = (
                    f"Database {database} has a Schema {schema}, Table {table_name}, "
                    f"Column {col}, containing distinct boolean values {value}."
                )
            elif inferred_type == 'date':
                stmt = (
                    f"Database {database} has a Schema {schema}, Table {table_name}, "
                    f"Column {col}, with date range from {value} to {value}."
                )
            else:
                stmt = (
                    f"Database {database} has a Schema {schema}, Table {table_name}, "
                    f"Column {col}, containing distinct text values {value}."
                )

            statements.append(stmt)

        return statements

    async def prepare_request_from_statements(self, statements: List[str]) -> Dict[str, Any]:
        """
        Reverse of 'prepare_statements'. Returns a reconstructed request
        """
        # Regex patterns
        text_pattern = re.compile(
            r'^Database\s+(?P<db>\S+)\s+has a Schema\s+(?P<schema>\S+),\s+Table\s+(?P<table>\S*?),\s+'
            r'Column\s+(?P<col>\S+),\s+containing distinct text values\s+(?P<value>.*)\.$'
        )

        numeric_pattern = re.compile(
            r'^Database\s+(?P<db>\S+)\s+has a Schema\s+(?P<schema>\S+),\s+Table\s+(?P<table>\S*?),\s+'
            r'Column\s+(?P<col>\S+),\s+with mean\s+(?P<mean>[\d\.]+)\s+and standard deviation\s+(?P<std>[\d\.]+).*'
        )

        boolean_pattern = re.compile(
            r'^Database\s+(?P<db>\S+)\s+has a Schema\s+(?P<schema>\S+),\s+Table\s+(?P<table>\S*?),\s+'
            r'Column\s+(?P<col>\S+),\s+containing distinct boolean values\s+(?P<value>.*)\.$'
        )

        date_pattern = re.compile(
            r'^Database\s+(?P<db>\S+)\s+has a Schema\s+(?P<schema>\S+),\s+Table\s+(?P<table>\S*?),\s+'
            r'Column\s+(?P<col>\S+),\s+with date range from\s+(?P<min_val>.+?)\s+to\s+(?P<max_val>.+)\.$'
        )

        parameters: Dict[str, Dict[str, Any]] = {}
        data_api = None

        for statement in statements:
            match_text = text_pattern.match(statement)
            match_num = numeric_pattern.match(statement)
            match_bool = boolean_pattern.match(statement)
            match_date = date_pattern.match(statement)

            if match_text:
                g = match_text.groupdict()
                col = g["col"]
                table = g["table"]
                if data_api is None:
                    data_api = g["schema"]

                val_str = g["value"].strip()
                val_list = [v.strip() for v in val_str.split(",")]

                parameters[col] = {
                    "table": table,
                    "values": val_list
                }

            elif match_num:
                g = match_num.groupdict()
                col = g["col"]
                table = g["table"]
                if data_api is None:
                    data_api = g["schema"]

                mean_str = g["mean"]
                std_str = g["std"]
                try:
                    mean_f = float(mean_str)
                except ValueError:
                    mean_f = 0.0
                try:
                    std_f = float(std_str)
                except ValueError:
                    std_f = 0.0

                parameters[col] = {
                    "table": table,
                    "mean": mean_f,
                    "std": std_f
                }

            elif match_bool:
                g = match_bool.groupdict()
                col = g["col"]
                table = g["table"]
                if data_api is None:
                    data_api = g["schema"]

                val_str = g["value"].strip()
                val_list = [v.strip() for v in val_str.split(",")]

                parameters[col] = {
                    "table": table,
                    "values": val_list
                }

            elif match_date:
                g = match_date.groupdict()
                col = g["col"]
                table = g["table"]
                if data_api is None:
                    data_api = g["schema"]

                raw_min_val = g["min_val"].strip()
                raw_max_val = g["max_val"].strip()

                min_parsed = simplify_date(raw_min_val)
                max_parsed = simplify_date(raw_max_val)

                parameters[col] = {
                    "table": table,
                    "min": min_parsed,
                    "max": max_parsed
                }

            else:
                Logger.warning(f"Could not parse statement: {statement}")

        return {
            "data_api": data_api if data_api else "",
            "parameters": parameters,
            "operator": "and"
        }

    async def prepare_statements_from_bigquery(
        self,
        tenant_slug: str,
        rows: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """
        Generate natural language stories from BigQuery metadata rows.
        Uses source column to determine story type (event/profile/billing).

        :param tenant_slug: Tenant identifier
        :param rows: List of metadata rows from the aggregated metadata table
        :return: List of dictionaries with keys: 'statement', 'tenant_id', 'source'
        """
        statements = []
        for row in rows:
            source = row.get('source')

            if source == 'event':
                statements.extend(self._generate_event_stories(tenant_slug, row))
            elif source == 'profile':
                statements.extend(self._generate_profile_stories(tenant_slug, row))
            elif source == 'billing':
                statements.extend(self._generate_billing_stories(tenant_slug, row))
            else:
                Logger.warning(f"Unknown source type: {source}")

        return statements

    def _generate_event_stories(self, tenant_slug: str, row: Dict[str, Any]) -> List[Dict[str, str]]:
        """Generate consolidated single statement for event rows (source='event')."""
        event_name = row.get('event_name')
        if not event_name:
            return []

        # Build single consolidated statement
        statement_parts = [f"Tenant {tenant_slug} has Event {event_name}"]

        # Add event tag if present
        event_tag = row.get('event_tag')
        if event_tag:
            statement_parts.append(f"with tag {event_tag}")

        # Check if funnel stages are present (this will be used as identifier)
        funnel_stages = row.get('funnel_stage')
        has_funnel_stage = False
        if funnel_stages and isinstance(funnel_stages, list):
            valid_stages = [stage for stage in funnel_stages if stage]
            if valid_stages:
                has_funnel_stage = True
                stages_str = ", ".join(valid_stages)
                statement_parts.append(f"in funnel {stages_str}")

        # Add property name if present
        property_name = row.get('property_name')
        if property_name:
            statement_parts.append(f"with property {property_name}")

        # Add values (categorical/numeric/date) if present
        top_values = row.get('top_values')
        numeric_mean = row.get('numeric_mean')
        numeric_stddev = row.get('numeric_stddev')
        date_min = row.get('date_min')
        date_max = row.get('date_max')

        if top_values and isinstance(top_values, list) and numeric_mean is None:
            # Categorical values
            top_5_values = [str(v) for v in top_values[:5] if v]
            if top_5_values:
                values_str = ", ".join(top_5_values)
                statement_parts.append(f"containing values like {values_str}")
        elif numeric_mean is not None and numeric_stddev is not None:
            # Numeric stats
            statement_parts.append(f"with mean {numeric_mean:.2f} and standard deviation {numeric_stddev:.2f}")
        elif date_min or date_max:
            # Date range
            statement_parts.append(f"with date range from {date_min} to {date_max}")

        # Join all parts and return structured data
        # Only add has_funnel_stage field if it's True (events with funnel stages)
        final_statement = " ".join(statement_parts) + "."
        result = {
            'statement': final_statement,
            'tenant_id': tenant_slug,
            'source': 'event'
        }
        if has_funnel_stage:
            result['has_funnel_stage'] = True

        return [result]

    def _generate_profile_stories(self, tenant_slug: str, row: Dict[str, Any]) -> List[Dict[str, str]]:
        """Generate consolidated single statement for profile property rows (source='profile')."""
        property_name = row.get('property_name')
        if not property_name:
            return []

        # Check cardinality early for categorical fields
        cardinality_band = row.get('cardinality_band')
        top_values = row.get('top_values')
        numeric_mean = row.get('numeric_mean')

        # Skip entire property if categorical with high/extreme cardinality
        if top_values and isinstance(top_values, list) and numeric_mean is None:
            if cardinality_band and cardinality_band.lower() in ['high', 'extreme']:
                Logger.info(f"Skipping entire property {property_name} due to {cardinality_band} cardinality")
                return []

        # Build single consolidated statement
        statement_parts = [f"Tenant {tenant_slug} has Profile Property {property_name}"]

        # Add property tag if present
        property_tag = row.get('property_tag')
        if property_tag:
            statement_parts.append(f"with tag {property_tag}")

        # Add values (categorical/numeric/date) - REQUIRED
        numeric_stddev = row.get('numeric_stddev')
        date_min = row.get('date_min')
        date_max = row.get('date_max')

        value_added = False
        if top_values and isinstance(top_values, list) and numeric_mean is None:
            # Categorical values
            top_5_values = [str(v) for v in top_values[:5] if v]
            if top_5_values:
                values_str = ", ".join(top_5_values)
                statement_parts.append(f"containing values like {values_str}")
                value_added = True
        elif numeric_mean is not None and numeric_stddev is not None:
            # Numeric stats
            statement_parts.append(f"with mean {numeric_mean:.2f} and standard deviation {numeric_stddev:.2f}")
            value_added = True
        elif date_min or date_max:
            # Date range
            statement_parts.append(f"with date range from {date_min} to {date_max}")
            value_added = True

        # Only return statement if we have values
        if not value_added:
            return []

        # Join all parts and return structured data
        final_statement = " ".join(statement_parts) + "."
        return [{
            'statement': final_statement,
            'tenant_id': tenant_slug,
            'source': 'profile'
        }]

    def _generate_billing_stories(self, tenant_slug: str, row: Dict[str, Any]) -> List[Dict[str, str]]:
        """Generate consolidated single statement for billing property rows (source='billing')."""
        property_name = row.get('property_name')
        if not property_name:
            return []

        # Check cardinality early for categorical fields
        cardinality_band = row.get('cardinality_band')
        top_values = row.get('top_values')
        numeric_mean = row.get('numeric_mean')

        # Skip entire property if categorical with high/extreme cardinality
        if top_values and isinstance(top_values, list) and numeric_mean is None:
            if cardinality_band and cardinality_band.lower() in ['high', 'extreme']:
                Logger.info(f"Skipping entire billing property {property_name} due to {cardinality_band} cardinality")
                return []

        # Build single consolidated statement
        statement_parts = [f"Tenant {tenant_slug} has Billing Property {property_name}"]

        # Add property tag if present
        property_tag = row.get('property_tag')
        if property_tag:
            statement_parts.append(f"with tag {property_tag}")

        # Add values (categorical/numeric/date) - REQUIRED
        numeric_stddev = row.get('numeric_stddev')
        date_min = row.get('date_min')
        date_max = row.get('date_max')

        value_added = False
        if top_values and isinstance(top_values, list) and numeric_mean is None:
            # Categorical values
            top_5_values = [str(v) for v in top_values[:5] if v]
            if top_5_values:
                values_str = ", ".join(top_5_values)
                statement_parts.append(f"containing values like {values_str}")
                value_added = True
        elif numeric_mean is not None and numeric_stddev is not None:
            # Numeric stats
            statement_parts.append(f"with mean {numeric_mean:.2f} and standard deviation {numeric_stddev:.2f}")
            value_added = True
        elif date_min or date_max:
            # Date range
            statement_parts.append(f"with date range from {date_min} to {date_max}")
            value_added = True

        # Only return statement if we have values
        if not value_added:
            return []

        # Join all parts and return structured data
        final_statement = " ".join(statement_parts) + "."
        return [{
            'statement': final_statement,
            'tenant_id': tenant_slug,
            'source': 'billing'
        }]
