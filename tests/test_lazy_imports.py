"""
Verify that importing the package does not require the heavy optional
runtime dependencies (sentence-transformers, weaviate-client).

A subprocess with a meta-path import blocker simulates an environment
where those packages are not installed.
"""
import subprocess
import sys
import textwrap


BLOCKER_SCRIPT = textwrap.dedent("""
    import importlib.abc
    import sys

    BLOCKED = ("sentence_transformers", "weaviate")

    class Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            root = fullname.split(".")[0]
            if root in BLOCKED:
                raise ImportError(f"import of {fullname!r} blocked for test")
            return None

    sys.meta_path.insert(0, Blocker())

    import semantic_search
    from semantic_search import (
        PostgresSchemaScanner,
        BigQueryMetadataScanner,
        SchemaTransformer,
        WeaviateLoader,
        SemanticSearch,
        simplify_date,
        infer_value_type,
        similarity_score,
        find_best_match,
    )

    # Constructing the loader must also work without the heavy deps;
    # only using the model / client requires them.
    loader = WeaviateLoader()
    print("OK")
""")


def test_import_succeeds_without_sentence_transformers_or_weaviate():
    result = subprocess.run(
        [sys.executable, "-c", BLOCKER_SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
