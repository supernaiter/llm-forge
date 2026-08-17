import pytest

from forge.traceability import (
    TRACEABILITY_PATH,
    TraceabilityError,
    load_traceability,
    traceability_sha256,
    validate_traceability,
)


def test_traceability_matrix_covers_protocol_requirements_and_paths():
    matrix = load_traceability()
    assert TRACEABILITY_PATH.is_file()
    assert matrix["matrix_id"] == "FORGE_TRACEABILITY_V3"
    assert len(traceability_sha256()) == 64


def test_traceability_matrix_fails_closed_on_missing_requirement():
    matrix = load_traceability()
    matrix["entries"] = matrix["entries"][:-1]
    with pytest.raises(TraceabilityError):
        validate_traceability(matrix)


def test_traceability_matrix_rejects_unresolvable_evidence_path():
    matrix = load_traceability()
    matrix["entries"][0]["implementation_evidence"].append("does/not/exist.py")
    with pytest.raises(TraceabilityError):
        validate_traceability(matrix)
