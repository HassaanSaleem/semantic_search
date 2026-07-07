from events_repository import DatetimeValidator
from difflib import SequenceMatcher
import datetime


def infer_value_type(value):
    """
    Infers the column type based on the input value:
    Returns a tuple: (inferred_type, transformed_value)
    """
    try:
        numeric_val = float(value)
        return 'numeric', numeric_val
    except ValueError:
        pass

    str_value = str(value).strip().lower()
    if str_value in ['true', 'false']:
        return 'boolean', str_value
    if DatetimeValidator.is_valid(value):
        return 'date', DatetimeValidator.from_value(value)

    return 'text', value


def simplify_date(date_str: str) -> str:
    """
    Attempt to parse the date_str using DatetimeValidator.
    Return 'YYYY-MM-DD' if possible; otherwise return the original string.
    """
    dt_obj = DatetimeValidator.from_value(date_str)
    if dt_obj is None:
        return date_str

    if isinstance(dt_obj, datetime.datetime):
        return dt_obj.strftime("%Y-%m-%d")
    elif isinstance(dt_obj, datetime.date):
        return dt_obj.isoformat()
    else:
        return str(dt_obj)


def similarity_score(a: str, b: str) -> float:
    """
    Returns a similarity score (0.0 - 100.0) between two strings.
    """
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100.0


def find_best_match(target: str, candidates: list[str]) -> tuple[str, float]:
    """
    Return (best_candidate, best_score) from candidates that best matches the target string
    """
    best_candidate = ""
    best_score = 0.0
    for c in candidates:
        sc = similarity_score(target, c)
        if sc > best_score:
            best_score = sc
            best_candidate = c
    return best_candidate, best_score
