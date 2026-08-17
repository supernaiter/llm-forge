"""Crash-safe, hash-chained attempt/event ledger for Research V3.

The legacy archive intentionally stores survivors only.  This ledger records
every generation slot, including model failures and rejected candidates, so an
attempt-indexed metric cannot silently discard hard cases.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path
from typing import Any, Mapping

from .protocol import canonical_json, strict_json_loads
from .resources import ResourceError, empty_usage, normalize_usage, summarize_resources
from .lineage import lineage_audit


ATTEMPT_STATUSES = frozenset({
    "started",
    "valid_candidate",
    "model_error",
    "empty_response",
    "duplicate_candidate",
    "invalid_syntax",
    "constraint_violation",
    "runtime_error",
    "timeout",
    "evaluation_hack",
    "sandbox_rejected",
    "evaluator_rejected",
    "evaluator_budget_exhausted",
})
# Keep the event vocabulary closed.  An unknown event would otherwise be
# included in the hash chain but ignored by replay, allowing a malformed or
# hidden state transition to survive a superficially valid ledger audit.
EVENT_TYPES = frozenset({
    "attempt_started",
    "attempt_finished",
    "evaluation_completed",
    "incumbent_selected",
})
ALLOWED_TRACKS = frozenset({"SAME_MODEL", "NATIVE_COMPUTE"})
_FORBIDDEN_MODEL_ALIASES = frozenset({"latest", "default", "floating", "unpinned"})


class LedgerError(ValueError):
    """Raised when an event ledger is malformed or violates its invariants."""


def _hash_event(event: Mapping[str, Any]) -> str:
    body = {key: value for key, value in event.items() if key != "event_hash"}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def _reject_nonfinite(value: Any, path: str = "payload") -> None:
    """Reject JSON numbers that cannot participate in deterministic replay."""
    if isinstance(value, float) and not math.isfinite(value):
        raise LedgerError(f"non-finite number in {path}")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _reject_nonfinite(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_nonfinite(child, f"{path}[{index}]")


class EventLedger:
    """Append-only JSONL event log with a tamper-evident hash chain.

    A ledger is either valid in its entirety or unusable.  In particular, a
    truncated final line and an unfinished attempt are not silently ignored.
    """

    schema_version = 1

    def __init__(self, path: str | Path, *, run_id: str | None = None,
                 max_attempts: int | None = None,
                 resource_budgets: Mapping[str, Any] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or self.path.parent.name or str(uuid.uuid4())
        self.max_attempts = max_attempts
        self.resource_budgets = dict(resource_budgets or {})
        self.events: list[dict[str, Any]] = []
        self._last_hash = "0" * 64
        self._started: dict[str, dict[str, Any]] = {}
        self._finished: dict[str, dict[str, Any]] = {}
        self._incumbent_selected: dict[str, dict[str, Any]] = {}
        # A generation slot is the unit charged by the V3 attempt budget.
        # Keep an explicit index so a malformed/replayed stream cannot charge
        # two attempts to the same slot (or use an invalid slot coordinate).
        self._slots: dict[tuple[int, int], str] = {}
        self._resource_records: list[dict[str, Any]] = []
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        with self.path.open("rb") as fh:
            for line_no, raw in enumerate(fh, 1):
                if not raw.endswith(b"\n"):
                    raise LedgerError(f"truncated ledger line {line_no}")
                try:
                    event = strict_json_loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise LedgerError(f"invalid ledger JSON at line {line_no}") from exc
                except ValueError as exc:
                    raise LedgerError(
                        f"invalid ledger JSON at line {line_no}: {exc}"
                    ) from exc
                self._validate_event(event, line_no)
                self.events.append(event)
                self._index_event(event)
                self._last_hash = event["event_hash"]
        self.assert_invariants(require_finished=False)

    def _validate_event(self, event: Mapping[str, Any], line_no: int) -> None:
        required = {"schema_version", "seq", "run_id", "event_type", "payload",
                    "prev_hash", "event_hash"}
        missing = sorted(required - set(event))
        if missing:
            raise LedgerError(f"ledger line {line_no} missing: {', '.join(missing)}")
        if event["schema_version"] != self.schema_version:
            raise LedgerError(f"unsupported ledger schema at line {line_no}")
        if not isinstance(event["seq"], int) or isinstance(event["seq"], bool):
            raise LedgerError(f"event sequence must be an integer at line {line_no}")
        if not isinstance(event["run_id"], str) or not event["run_id"]:
            raise LedgerError(f"event run_id must be a non-empty string at line {line_no}")
        if not isinstance(event["event_type"], str) or not event["event_type"]:
            raise LedgerError(f"event_type must be a non-empty string at line {line_no}")
        if event["event_type"] not in EVENT_TYPES:
            raise LedgerError(f"unknown event_type at line {line_no}: {event['event_type']}")
        if not isinstance(event["payload"], Mapping):
            raise LedgerError(f"event payload must be an object at line {line_no}")
        _reject_nonfinite(event["payload"])
        if not isinstance(event["prev_hash"], str) or not isinstance(
            event["event_hash"], str
        ):
            raise LedgerError(f"event hashes must be strings at line {line_no}")
        if event["run_id"] != self.run_id:
            raise LedgerError(f"run_id mismatch at line {line_no}")
        if event["seq"] != len(self.events) + 1:
            raise LedgerError(f"sequence mismatch at line {line_no}")
        if event["prev_hash"] != self._last_hash:
            raise LedgerError(f"hash-chain predecessor mismatch at line {line_no}")
        if event["event_hash"] != _hash_event(event):
            raise LedgerError(f"event hash mismatch at line {line_no}")

    def _index_event(self, event: Mapping[str, Any]) -> None:
        payload = event["payload"]
        if event["event_type"] == "attempt_started":
            attempt_id = payload.get("attempt_id")
            if not isinstance(attempt_id, str) or not attempt_id:
                raise LedgerError("attempt_started requires attempt_id")
            if attempt_id in self._started:
                raise LedgerError(f"duplicate attempt start: {attempt_id}")
            expected_attempt_id = f"{self.run_id}:attempt:{len(self._started) + 1}"
            if attempt_id != expected_attempt_id:
                raise LedgerError(
                    f"attempt_id is not the next run sequence: expected {expected_attempt_id}"
                )
            track = payload.get("track")
            if not isinstance(track, str) or track not in ALLOWED_TRACKS:
                raise LedgerError("attempt_started requires one valid V3 track")
            model = payload.get("model")
            if model is not None and (
                not isinstance(model, str)
                or not model.strip()
                or model.strip().lower() in _FORBIDDEN_MODEL_ALIASES
            ):
                raise LedgerError("attempt_started model identity is not pinned")
            generation = payload.get("generation")
            slot = payload.get("slot")
            if (
                isinstance(generation, bool)
                or not isinstance(generation, int)
                or generation < 0
                or isinstance(slot, bool)
                or not isinstance(slot, int)
                or slot < 0
            ):
                raise LedgerError("attempt_started requires non-negative integer generation and slot")
            slot_key = (generation, slot)
            if slot_key in self._slots:
                previous = self._slots[slot_key]
                raise LedgerError(
                    f"duplicate generation slot {generation}:{slot} "
                    f"(attempts {previous} and {attempt_id})"
                )
            persisted_budgets = payload.get("resource_budgets")
            if persisted_budgets is not None:
                if not isinstance(persisted_budgets, Mapping):
                    raise LedgerError("resource_budgets must be an object")
                persisted_budgets = dict(persisted_budgets)
                if self.resource_budgets and self.resource_budgets != persisted_budgets:
                    raise LedgerError("resource budget metadata mismatch")
                self.resource_budgets = persisted_budgets
            self._started[attempt_id] = dict(payload)
            self._slots[slot_key] = attempt_id
        elif event["event_type"] == "attempt_finished":
            attempt_id = payload.get("attempt_id")
            if not isinstance(attempt_id, str) or not attempt_id:
                raise LedgerError("attempt_finished requires attempt_id")
            if attempt_id not in self._started or attempt_id in self._finished:
                raise LedgerError(f"invalid attempt finish: {attempt_id}")
            status = payload.get("status")
            if status not in ATTEMPT_STATUSES - {"started"}:
                raise LedgerError(f"invalid attempt status: {status}")
            candidate_hash = payload.get("candidate_sha256")
            if candidate_hash is not None and (
                not isinstance(candidate_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", candidate_hash) is None
            ):
                raise LedgerError("attempt_finished candidate hash is not lowercase SHA-256")
            score = payload.get("score")
            if score is not None and (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
            ):
                raise LedgerError("attempt_finished score must be finite")
            error_class = payload.get("error_class")
            if error_class is not None and (
                not isinstance(error_class, str) or not error_class.strip()
            ):
                raise LedgerError("attempt_finished error_class must be a non-empty string")
            metadata = payload.get("metadata")
            if metadata is not None and not isinstance(metadata, Mapping):
                raise LedgerError("attempt_finished metadata must be an object")
            self._finished[attempt_id] = dict(payload)
            try:
                usage = normalize_usage(payload.get("resource_usage"))
            except ResourceError as exc:
                raise LedgerError(f"invalid generation resource usage: {exc}") from exc
            # A controller-selected model identity is part of the causal
            # search record. If the adapter reports an observed identity, it
            # must agree; otherwise the ledger could make a wrong model look
            # like the pinned controller choice. Missing telemetry remains
            # explicit and is rejected later by the registered-study gates.
            started_metadata = self._started[attempt_id].get("metadata")
            controller_action = (
                started_metadata.get("controller_action")
                if isinstance(started_metadata, Mapping) else None
            )
            generation_mode = (
                started_metadata.get("generation_mode")
                if isinstance(started_metadata, Mapping) else None
            )
            if generation_mode is not None and generation_mode not in {"llm", "parametric"}:
                raise LedgerError("attempt generation_mode is unknown")
            mock_execution = (
                started_metadata.get("mock_execution") is True
                if isinstance(started_metadata, Mapping) else False
            )
            expected_model = (
                controller_action.get("generator_model")
                if isinstance(controller_action, Mapping) else None
            )
            observed_model = usage.get("model_identity")
            if expected_model is not None and (
                not isinstance(expected_model, str) or not expected_model.strip()
            ):
                raise LedgerError("controller action generator_model is invalid")
            if generation_mode == "parametric" and observed_model != "PARAM_MUTATION":
                raise LedgerError(
                    "parametric generation resource identity must be PARAM_MUTATION"
                )
            if (
                generation_mode != "parametric"
                and expected_model is not None
                and observed_model is not None
                and observed_model != expected_model
                and not (mock_execution and observed_model == "MOCK")
            ):
                raise LedgerError(
                    "generation resource model identity differs from controller action"
                )
            self._resource_records.append({
                "phase": "generation",
                "attempt_id": attempt_id,
                "usage": usage,
            })
            if "evaluator_resource_usage" in payload:
                try:
                    evaluator_usage = normalize_usage(payload.get("evaluator_resource_usage"))
                except ResourceError as exc:
                    raise LedgerError(f"invalid evaluator resource usage: {exc}") from exc
                self._resource_records.append({
                    "phase": "evaluator",
                    "attempt_id": attempt_id,
                    "usage": evaluator_usage,
                })
        elif event["event_type"] == "evaluation_completed":
            attempt_id = payload.get("attempt_id")
            if not isinstance(attempt_id, str) or not attempt_id:
                raise LedgerError("evaluation_completed requires attempt_id")
            scope = payload.get("scope")
            if scope is not None and scope != "run":
                raise LedgerError("evaluation_completed scope must be 'run' when present")
            if attempt_id not in self._started and scope != "run":
                raise LedgerError(f"evaluation references unknown attempt: {attempt_id}")
            status = payload.get("status")
            if not isinstance(status, str) or not status.strip():
                raise LedgerError("evaluation_completed requires a non-empty status")
            try:
                usage = normalize_usage(payload.get("resource_usage"))
            except ResourceError as exc:
                raise LedgerError(f"invalid evaluator resource usage: {exc}") from exc
            self._resource_records.append({
                "phase": "evaluator",
                "attempt_id": attempt_id,
                "usage": usage,
            })
        elif event["event_type"] == "incumbent_selected":
            attempt_id = payload.get("attempt_id")
            if not isinstance(attempt_id, str) or attempt_id not in self._finished:
                raise LedgerError(
                    "incumbent_selected must reference a finished attempt"
                )
            if attempt_id in self._incumbent_selected:
                raise LedgerError(f"duplicate incumbent checkpoint: {attempt_id}")
            after_attempt = payload.get("after_attempt")
            attempt_position = list(self._started).index(attempt_id) + 1
            if (
                isinstance(after_attempt, bool)
                or not isinstance(after_attempt, int)
                or after_attempt != attempt_position
            ):
                raise LedgerError(
                    "incumbent_selected after_attempt must equal the attempt sequence"
                )
            candidate_hash = payload.get("candidate_sha256")
            if (
                not isinstance(candidate_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", candidate_hash) is None
            ):
                raise LedgerError(
                    "incumbent_selected requires a lowercase SHA-256 candidate digest"
                )
            score = payload.get("score")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
            ):
                raise LedgerError("incumbent_selected requires a finite score")
            self._incumbent_selected[attempt_id] = dict(payload)

    def _append(self, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        event = {
            "schema_version": self.schema_version,
            "seq": len(self.events) + 1,
            "run_id": self.run_id,
            "event_type": event_type,
            "payload": dict(payload),
            "prev_hash": self._last_hash,
        }
        event["event_hash"] = _hash_event(event)
        encoded = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        # Validate and index before exposing the event on disk.  A malformed
        # event (for example a duplicate checkpoint) must not leave a line
        # behind that makes the ledger unrecoverable after the caller catches
        # the exception.  If the durable write itself fails, restore the
        # in-memory indexes and truncate any partial append.
        self._validate_event(event, len(self.events) + 1)
        snapshot = (
            dict(self._started),
            dict(self._finished),
            dict(self._incumbent_selected),
            dict(self._slots),
            list(self._resource_records),
            dict(self.resource_budgets),
        )
        original_size = self.path.stat().st_size if self.path.exists() else 0
        try:
            self._index_event(event)
            with self.path.open("ab") as fh:
                fh.write(encoded)
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:
            (
                self._started,
                self._finished,
                self._incumbent_selected,
                self._slots,
                self._resource_records,
                self.resource_budgets,
            ) = snapshot
            try:
                with self.path.open("r+b") as fh:
                    fh.truncate(original_size)
            except OSError:
                pass
            raise
        self.events.append(event)
        self._last_hash = event["event_hash"]
        return event

    @property
    def attempt_count(self) -> int:
        return len(self._started)

    @property
    def finished_attempt_count(self) -> int:
        return len(self._finished)

    @property
    def open_attempt_ids(self) -> tuple[str, ...]:
        return tuple(sorted(set(self._started) - set(self._finished)))

    def record_event(self, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._append(event_type, payload)

    def start_attempt(self, *, generation: int, slot: int,
                      model: str | None = None, track: str = "SAME_MODEL",
                      metadata: Mapping[str, Any] | None = None) -> str:
        if self.max_attempts is not None and self.attempt_count >= self.max_attempts:
            raise LedgerError("attempt cap exhausted")
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
            or isinstance(slot, bool)
            or not isinstance(slot, int)
            or slot < 0
        ):
            raise LedgerError("generation and slot must be non-negative integers")
        if (generation, slot) in self._slots:
            raise LedgerError(f"generation slot already recorded: {generation}:{slot}")
        if track not in ALLOWED_TRACKS:
            raise LedgerError(f"unknown V3 track: {track}")
        if model is not None and (
            not isinstance(model, str)
            or not model.strip()
            or model.strip().lower() in _FORBIDDEN_MODEL_ALIASES
        ):
            raise LedgerError("model identity must be a pinned non-empty value")
        attempt_id = f"{self.run_id}:attempt:{self.attempt_count + 1}"
        payload: dict[str, Any] = {
            "attempt_id": attempt_id,
            "generation": generation,
            "slot": slot,
            "track": track,
        }
        if model is not None:
            payload["model"] = model
        if self.resource_budgets:
            payload["resource_budgets"] = self.resource_budgets
        if metadata:
            payload["metadata"] = dict(metadata)
        self._append("attempt_started", payload)
        return attempt_id

    def finish_attempt(self, attempt_id: str, *, status: str,
                       candidate_hash: str | None = None,
                       score: float | None = None,
                       error_class: str | None = None,
                       resource_usage: Mapping[str, Any] | None = None,
                       evaluator_resource_usage: Mapping[str, Any] | None = None,
                       metadata: Mapping[str, Any] | None = None) -> None:
        if status not in ATTEMPT_STATUSES - {"started"}:
            raise LedgerError(f"unknown attempt status: {status}")
        if attempt_id not in self._started:
            raise LedgerError(f"attempt was not started: {attempt_id}")
        try:
            normalized_usage = normalize_usage(resource_usage)
        except ResourceError as exc:
            raise LedgerError(f"invalid generation resource usage: {exc}") from exc
        self._assert_prospective_resources("generation", attempt_id, normalized_usage)
        normalized_evaluator_usage = None
        if evaluator_resource_usage is not None:
            try:
                normalized_evaluator_usage = normalize_usage(evaluator_resource_usage)
            except ResourceError as exc:
                raise LedgerError(f"invalid evaluator resource usage: {exc}") from exc
            self._assert_prospective_resources(
                "evaluator", attempt_id, normalized_evaluator_usage
            )
        payload: dict[str, Any] = {
            "attempt_id": attempt_id,
            "status": status,
            "resource_usage": normalized_usage,
        }
        if normalized_evaluator_usage is not None:
            payload["evaluator_resource_usage"] = normalized_evaluator_usage
        if candidate_hash is not None:
            payload["candidate_sha256"] = candidate_hash
        if score is not None:
            payload["score"] = score
        if error_class is not None:
            payload["error_class"] = error_class
        if metadata:
            payload["metadata"] = dict(metadata)
        self._append("attempt_finished", payload)

    def record_evaluation(self, attempt_id: str, *,
                          resource_usage: Mapping[str, Any] | None = None,
                          status: str = "completed",
                          allow_unbound: bool = False) -> None:
        """Record evaluator cost separately from generation cost.

        An evaluator record is allowed to contain an observed zero cost when
        no evaluator was invoked.  Missing telemetry remains explicit and is
        never converted to zero by this method.
        """
        if attempt_id not in self._started and not allow_unbound:
            raise LedgerError(f"evaluation references unknown attempt: {attempt_id}")
        try:
            normalized_usage = normalize_usage(resource_usage)
        except ResourceError as exc:
            raise LedgerError(f"invalid evaluator resource usage: {exc}") from exc
        self._assert_prospective_resources("evaluator", attempt_id, normalized_usage)
        payload = {
            "attempt_id": attempt_id,
            "status": status,
            "resource_usage": normalized_usage,
        }
        if allow_unbound:
            payload["scope"] = "run"
        self._append("evaluation_completed", payload)

    def _assert_prospective_resources(self, phase: str, attempt_id: str,
                                      usage: Mapping[str, Any]) -> None:
        """Reject a resource-budget violation before appending its event."""
        prospective = [*self._resource_records, {
            "phase": phase,
            "attempt_id": attempt_id,
            "usage": dict(usage),
        }]
        resource = summarize_resources(
            prospective,
            budgets=self.resource_budgets,
            require_gpu=self._requires_gpu_telemetry(),
        )
        if resource["errors"]:
            raise LedgerError("resource ledger invalid: " + "; ".join(resource["errors"]))
        if (
            self._requires_gpu_telemetry()
            and resource["phases"]["generation"]["required_missing_fields"]
        ):
            raise LedgerError(
                "native generation telemetry missing: "
                + ", ".join(resource["phases"]["generation"]["required_missing_fields"])
            )
        if resource["budget_violations"]:
            raise LedgerError(
                "resource budget exceeded: " + ", ".join(resource["budget_violations"])
            )

    def assert_invariants(
        self, *, require_finished: bool = True, require_checkpoints: bool = False
    ) -> None:
        if self.max_attempts is not None and self.attempt_count > self.max_attempts:
            raise LedgerError("attempt count exceeds cap")
        if require_finished and self.open_attempt_ids:
            raise LedgerError(f"unfinished attempts: {', '.join(self.open_attempt_ids)}")
        if self.finished_attempt_count > self.attempt_count:
            raise LedgerError("more finished than started attempts")
        if not self._incumbent_selected.keys() <= self._finished.keys():
            raise LedgerError("incumbent checkpoint references an unfinished attempt")
        if require_checkpoints:
            missing_checkpoints = sorted(
                set(self._finished) - set(self._incumbent_selected)
            )
            if missing_checkpoints:
                raise LedgerError(
                    "finished attempts missing incumbent checkpoints: "
                    + ", ".join(missing_checkpoints)
                )
        lineage = lineage_audit(self._finished.values())
        if lineage["lineage_cycle_count"]:
            raise LedgerError(
                "candidate lineage contains cycle(s): "
                + str(lineage["lineage_cycles"])
            )
        resource = summarize_resources(
            self._resource_records,
            budgets=self.resource_budgets,
            require_gpu=self._requires_gpu_telemetry(),
        )
        if resource["errors"]:
            raise LedgerError("resource ledger invalid: " + "; ".join(resource["errors"]))
        if (
            self._requires_gpu_telemetry()
            and resource["phases"]["generation"]["required_missing_fields"]
        ):
            raise LedgerError(
                "native generation telemetry missing: "
                + ", ".join(resource["phases"]["generation"]["required_missing_fields"])
            )
        if resource["budget_violations"]:
            raise LedgerError(
                "resource budget exceeded: " + ", ".join(resource["budget_violations"])
            )
        if require_finished:
            generation_ids = {
                record["attempt_id"] for record in self._resource_records
                if record["phase"] == "generation"
            }
            missing = sorted(set(self._finished) - generation_ids)
            if missing:
                raise LedgerError(
                    "finished attempts missing generation resources: " + ", ".join(missing)
                )

    def resource_summary(self) -> dict[str, Any]:
        """Return the separated generation/evaluator resource ledger view."""
        return summarize_resources(
            self._resource_records,
            budgets=self.resource_budgets,
            require_gpu=self._requires_gpu_telemetry(),
        )

    def _requires_gpu_telemetry(self) -> bool:
        tracks = {str(payload.get("track", "SAME_MODEL")) for payload in self._started.values()}
        return "NATIVE_COMPUTE" in tracks

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for event in self._finished.values():
            status = event["status"]
            counts[status] = counts.get(status, 0) + 1
        self.assert_invariants(require_finished=False)
        accepted = [event for event in self._finished.values()
                    if event.get("status") == "valid_candidate"]
        lineage = lineage_audit(self._finished.values())
        hack_attempts = sum(
            bool(event.get("metadata", {}).get("evaluator_hack_audit", {}).get("suspected_hack"))
            for event in self._finished.values()
        )
        ast_covered = sum(bool(event.get("metadata", {}).get("candidate_ast_sha256"))
                          for event in accepted)
        diff_covered = sum(
            isinstance(event.get("metadata", {}).get("accepted_candidate_diff_sha256"), list)
            for event in accepted
        )
        resource_summary = self.resource_summary()
        resource_hash = hashlib.sha256(
            canonical_json(resource_summary["records"])
        ).hexdigest()
        tracks = sorted({
            str(payload.get("track", "SAME_MODEL"))
            for payload in self._started.values()
        })
        return {
            "path": str(self.path),
            "run_id": self.run_id,
            "event_count": len(self.events),
            "attempt_count": self.attempt_count,
            "generation_slot_count": len(self._slots),
            "generation_slots": [
                {"generation": generation, "slot": slot, "attempt_id": attempt_id}
                for (generation, slot), attempt_id in sorted(self._slots.items())
            ],
            "finished_attempt_count": self.finished_attempt_count,
            "open_attempt_count": len(self.open_attempt_ids),
            "status_counts": counts,
            "accepted_candidate_count": len(accepted),
            "evaluator_hack_attempt_count": hack_attempts,
            "candidate_ast_hash_coverage": (
                ast_covered / len(accepted) if accepted else 1.0
            ),
            "accepted_candidate_diff_coverage": (
                diff_covered / len(accepted) if accepted else 1.0
            ),
            **{
                key: lineage[key]
                for key in (
                    "trace_parent_child_links_complete",
                    "parent_child_link_coverage",
                    "deterministic_cycle_detection_coverage",
                    "lineage_cycle_count",
                    "lineage_cycles",
                    "lineage_node_count",
                )
            },
            "evaluator_hack_audit_coverage": (
                sum(
                    isinstance(event.get("metadata", {}).get("evaluator_hack_audit"), Mapping)
                    for event in self._finished.values()
                ) / self.finished_attempt_count
                if self.finished_attempt_count else 1.0
            ),
            "resource_summary": resource_summary,
            "resource_ledger_hash": resource_hash,
            "tracks": tracks,
            "head_hash": self._last_hash,
        }


def candidate_sha256(candidate: str) -> str:
    return hashlib.sha256(candidate.encode("utf-8")).hexdigest()
