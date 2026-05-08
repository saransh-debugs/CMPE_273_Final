from __future__ import annotations

from enum import Enum


class SemanticFailureType(str, Enum):
    HALLUCINATED_IMPORT = "hallucinated_import"
    BAD_DELEGATION      = "bad_delegation"
    WRONG_TOOL_SELECTED = "wrong_tool_selected"
    JSON_PARSE_FAILURE  = "json_parse_failure"
    TIMEOUT             = "timeout"
    CONTEXT_OVERFLOW    = "context_overflow"
    DEPENDENCY_FAILURE  = "dependency_failure"
    UNKNOWN_ERROR       = "unknown_error"


def classify_error(error: BaseException, span_meta: dict) -> SemanticFailureType:
    msg = (repr(error) + " " + str(error)).lower()
    if "import" in msg or "modulenotfound" in msg or "hallucinated" in msg:
        return SemanticFailureType.HALLUCINATED_IMPORT
    if "timeout" in msg or "timed out" in msg:
        return SemanticFailureType.TIMEOUT
    if "context_length" in msg or "maximum context" in msg or "too many tokens" in msg:
        return SemanticFailureType.CONTEXT_OVERFLOW
    if "json" in msg or "jsondecode" in msg:
        return SemanticFailureType.JSON_PARSE_FAILURE
    if "delegation" in msg or "no such agent" in msg:
        return SemanticFailureType.BAD_DELEGATION
    return SemanticFailureType.UNKNOWN_ERROR
