from semantic_search import simplify_date, infer_value_type, similarity_score, find_best_match


class TestInferValueType:
    def test_integer_string_is_numeric(self):
        inferred, value = infer_value_type("42")
        assert inferred == "numeric"
        assert value == 42.0

    def test_float_string_is_numeric(self):
        inferred, value = infer_value_type("3.14")
        assert inferred == "numeric"
        assert value == 3.14

    def test_true_is_boolean(self):
        inferred, value = infer_value_type("True")
        assert inferred == "boolean"
        assert value == "true"

    def test_false_is_boolean(self):
        inferred, value = infer_value_type("FALSE")
        assert inferred == "boolean"
        assert value == "false"

    def test_iso_date_is_date(self):
        inferred, value = infer_value_type("2024-01-15")
        assert inferred == "date"
        assert value.startswith("2024-01-15")

    def test_plain_text_is_text(self):
        inferred, value = infer_value_type("subscription")
        assert inferred == "text"
        assert value == "subscription"


class TestSimplifyDate:
    def test_iso_date_is_normalized(self):
        # DatetimeValidator.from_value returns a formatted string, so
        # simplify_date passes it through as a string.
        result = simplify_date("2024-01-15")
        assert result.startswith("2024-01-15")

    def test_datetime_string_keeps_date_part(self):
        result = simplify_date("2024-01-15 10:30:00")
        assert result.startswith("2024-01-15")


class TestSimilarityScore:
    def test_identical_strings_score_100(self):
        assert similarity_score("alpha", "alpha") == 100.0

    def test_case_insensitive(self):
        assert similarity_score("Alpha", "ALPHA") == 100.0

    def test_disjoint_strings_score_0(self):
        assert similarity_score("abc", "xyz") == 0.0

    def test_partial_match_is_between_bounds(self):
        score = similarity_score("subscription", "subscriptions")
        assert 0.0 < score < 100.0


class TestFindBestMatch:
    def test_returns_exact_match(self):
        candidates = ["monthly", "yearly", "weekly"]
        best, score = find_best_match("yearly", candidates)
        assert best == "yearly"
        assert score == 100.0

    def test_returns_closest_candidate(self):
        candidates = ["monthly", "yearly", "weekly"]
        best, score = find_best_match("monthlyy", candidates)
        assert best == "monthly"
        assert score > 90.0

    def test_empty_candidates_returns_empty(self):
        best, score = find_best_match("anything", [])
        assert best == ""
        assert score == 0.0
