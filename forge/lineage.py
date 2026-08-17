"""Deterministic candidate ancestry and structural-edit digests."""
from __future__ import annotations

import ast
import difflib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .protocol import canonical_json, sha256_bytes


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def ast_sha256(source: str) -> str | None:
    """Hash a normalized AST, returning None for non-Python candidate text."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return None
    normalized = ast.dump(tree, annotate_fields=True, include_attributes=False)
    return sha256_bytes(normalized.encode("utf-8"))


def diff_sha256(parent: str, child: str) -> str:
    diff = "".join(difflib.unified_diff(
        parent.splitlines(keepends=True),
        child.splitlines(keepends=True),
        fromfile="parent",
        tofile="child",
    ))
    return sha256_bytes(diff.encode("utf-8"))


def lineage_metadata(candidate: str, parents: list[str]) -> dict[str, object]:
    return {
        "parent_candidate_sha256": [
            sha256_bytes(parent.encode("utf-8")) for parent in parents
        ],
        "candidate_ast_sha256": ast_sha256(candidate),
        "accepted_candidate_diff_sha256": [
            diff_sha256(parent, candidate) for parent in parents
        ],
        "parent_count": len(parents),
    }


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def detect_lineage_cycles(graph: Mapping[str, Iterable[str]]) -> tuple[tuple[str, ...], ...]:
    """Return deterministic cycle paths in a candidate parent graph.

    Parent hashes may refer to a seed candidate that is not present in the
    current ledger; such leaves are harmless.  Only cycles among recorded
    candidate nodes are reported.  The sorted traversal makes the audit
    reproducible regardless of JSON/event insertion order.
    """
    adjacency = {
        str(node): tuple(sorted({str(parent) for parent in parents if str(parent) in graph}))
        for node, parents in graph.items()
    }
    color: dict[str, int] = {}
    stack: list[str] = []
    cycles: set[tuple[str, ...]] = set()

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for parent in adjacency.get(node, ()):
            state = color.get(parent, 0)
            if state == 0:
                visit(parent)
            elif state == 1 and parent in stack:
                start = stack.index(parent)
                cycle = tuple(stack[start:] + [parent])
                cycles.add(cycle)
        stack.pop()
        color[node] = 2

    for node in sorted(adjacency):
        if color.get(node, 0) == 0:
            visit(node)
    return tuple(sorted(cycles))


def lineage_audit(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Recompute structural lineage coverage and cycle checks from attempts."""
    accepted = [
        record for record in records
        if isinstance(record, Mapping) and record.get("status") == "valid_candidate"
    ]
    graph: dict[str, set[str]] = {}
    complete = 0
    for record in accepted:
        candidate_hash = record.get("candidate_sha256")
        metadata = record.get("metadata")
        parents = metadata.get("parent_candidate_sha256") if isinstance(metadata, Mapping) else None
        diffs = metadata.get("accepted_candidate_diff_sha256") if isinstance(metadata, Mapping) else None
        parent_count = metadata.get("parent_count") if isinstance(metadata, Mapping) else None
        valid = (
            _valid_sha(candidate_hash)
            and isinstance(parents, list)
            and all(_valid_sha(parent) for parent in parents)
            and isinstance(diffs, list)
            and len(diffs) == len(parents)
            and all(_valid_sha(diff) for diff in diffs)
            and isinstance(parent_count, int)
            and not isinstance(parent_count, bool)
            and parent_count == len(parents)
        )
        if valid:
            complete += 1
            graph.setdefault(candidate_hash, set()).update(parents)
    cycles = detect_lineage_cycles(graph)
    coverage = complete / len(accepted) if accepted else 1.0
    return {
        "trace_parent_child_links_complete": complete == len(accepted),
        "parent_child_link_coverage": coverage,
        "deterministic_cycle_detection_coverage": coverage,
        "lineage_cycle_count": len(cycles),
        "lineage_cycles": [list(cycle) for cycle in cycles],
        "lineage_node_count": len(graph),
    }
