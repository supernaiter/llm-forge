from forge.lineage import (
    ast_sha256,
    detect_lineage_cycles,
    diff_sha256,
    lineage_audit,
    lineage_metadata,
)


def test_lineage_digests_are_deterministic_and_structural():
    parent = "def f(x):\n    return x\n"
    child = "def f(x):\n    return x + 1\n"
    assert ast_sha256(parent) != ast_sha256(child)
    assert diff_sha256(parent, child) == diff_sha256(parent, child)
    metadata = lineage_metadata(child, [parent])
    assert metadata["parent_count"] == 1
    assert metadata["candidate_ast_sha256"] == ast_sha256(child)
    assert len(metadata["parent_candidate_sha256"]) == 1


def test_non_python_candidate_has_explicit_missing_ast_digest():
    metadata = lineage_metadata("not python candidate", [])
    assert metadata["candidate_ast_sha256"] is None
    assert metadata["parent_count"] == 0


def test_lineage_audit_reports_complete_coverage_and_no_cycle():
    parent = "a" * 64
    child = "b" * 64
    records = [{
        "status": "valid_candidate",
        "candidate_sha256": child,
        "metadata": {
            "parent_candidate_sha256": [parent],
            "accepted_candidate_diff_sha256": ["c" * 64],
            "parent_count": 1,
        },
    }]
    audit = lineage_audit(records)
    assert audit["trace_parent_child_links_complete"] is True
    assert audit["parent_child_link_coverage"] == 1.0
    assert audit["deterministic_cycle_detection_coverage"] == 1.0
    assert audit["lineage_cycle_count"] == 0


def test_lineage_audit_detects_direct_cycle_and_incomplete_links():
    node = "d" * 64
    assert detect_lineage_cycles({node: {node}}) == ((node, node),)
    audit = lineage_audit([{
        "status": "valid_candidate",
        "candidate_sha256": node,
        "metadata": {"parent_count": 0},
    }])
    assert audit["trace_parent_child_links_complete"] is False
    assert audit["parent_child_link_coverage"] == 0.0
