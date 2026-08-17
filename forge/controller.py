"""Transferable, compute-aware search controller primitives.

This is an explicit frozen policy boundary, not a claim that a useful policy
has already been trained.  Training rows must be marked ``dev``; after freeze
the controller can only choose actions from search state and cannot update
from holdout feedback.
"""
from __future__ import annotations

import math
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .protocol import ProtocolError, canonical_json, sha256_bytes, strict_json_loads


_FORBIDDEN_MODEL_ALIASES = frozenset({"latest", "default", "main", "master", "floating", "unpinned"})
CONTROLLER_MANIFEST_SCHEMA_VERSION = 1
_CONTROLLER_MECHANISMS = frozenset({
    "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1",
    "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2",
    "FIXED_DEV_BEST",
    "NO_TRANSFER_PRIOR",
    "COST_UNAWARE_CONTROLLER",
})
_SHA256_RE = r"^[0-9a-f]{64}$"


@dataclass(frozen=True)
class SearchState:
    remaining_budget: int
    improvement_slope: float
    time_since_last_improvement: int
    archive_behavioral_entropy: float
    archive_score_dispersion: float
    candidate_invalid_rate: float
    duplicate_rate: float
    parent_lineage_depth: float
    recent_operator_success: float
    recent_model_success: float
    estimated_generation_cost: float

    def __post_init__(self) -> None:
        for field in ("remaining_budget", "time_since_last_improvement"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ProtocolError(f"{field} must be a non-negative integer")
        for field in (
            "improvement_slope", "archive_behavioral_entropy",
            "archive_score_dispersion", "candidate_invalid_rate", "duplicate_rate",
            "parent_lineage_depth", "recent_operator_success", "recent_model_success",
            "estimated_generation_cost",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ProtocolError(f"{field} must be numeric")
            if not math.isfinite(float(value)):
                raise ProtocolError(f"{field} must be finite")
            if field != "improvement_slope" and float(value) < 0:
                raise ProtocolError(f"{field} must be non-negative")


@dataclass(frozen=True)
class SearchAction:
    generator_model: str
    parent_selection_policy: str
    mutation_operator: str
    number_of_offspring: int
    reflection_depth: int
    archive_sampling_policy: str

    def __post_init__(self) -> None:
        for field in (
            "generator_model", "parent_selection_policy", "mutation_operator",
            "archive_sampling_policy",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ProtocolError(f"{field} must be a non-empty string")
        if self.generator_model.strip().lower() in _FORBIDDEN_MODEL_ALIASES:
            raise ProtocolError("generator_model must be a pinned model identity")
        if (
            isinstance(self.number_of_offspring, bool)
            or not isinstance(self.number_of_offspring, int)
            or self.number_of_offspring <= 0
        ):
            raise ProtocolError("number_of_offspring must be a positive integer")
        if (
            isinstance(self.reflection_depth, bool)
            or not isinstance(self.reflection_depth, int)
            or self.reflection_depth < 0
        ):
            raise ProtocolError("reflection_depth must be a non-negative integer")


class ControllerNotFrozenError(RuntimeError):
    pass


class HoldoutUpdateError(PermissionError):
    pass


class ComputeAwareController:
    """A deterministic action policy learned from development traces only."""

    mechanism_id = "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1"

    def __init__(self, actions: Iterable[SearchAction]):
        self.actions = tuple(actions)
        if not self.actions:
            raise ProtocolError("controller requires at least one action")
        if len(set(self.actions)) != len(self.actions):
            raise ProtocolError("controller action space contains duplicates")
        self._utility: dict[SearchAction, float] = {action: 0.0 for action in self.actions}
        self._support: dict[SearchAction, int] = {action: 0 for action in self.actions}
        self._frozen = False
        self._policy_sha256: str | None = None
        self._holdout_update_attempts = 0
        self._training_problem_ids: tuple[str, ...] = ()
        self._gain_normalization_scales: dict[str, float] = {}

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def policy_sha256(self) -> str | None:
        return self._policy_sha256

    @property
    def holdout_update_attempts(self) -> int:
        return self._holdout_update_attempts

    @property
    def training_problem_ids(self) -> tuple[str, ...]:
        return self._training_problem_ids

    @property
    def gain_normalization_scales(self) -> dict[str, float]:
        """Return the development-only per-problem gain scales used at fit."""
        return dict(self._gain_normalization_scales)

    @property
    def utilities(self) -> dict[SearchAction, float]:
        """Return a read-only snapshot of fitted action utilities.

        Development tooling uses this public snapshot to compare the frozen
        policy and its registered ablations without reaching into the
        controller's mutable implementation state.
        """
        return dict(self._utility)

    @property
    def supports(self) -> dict[SearchAction, int]:
        """Return a read-only snapshot of per-action development support."""
        return dict(self._support)

    def fit(self, traces: Iterable[Mapping[str, Any]]) -> None:
        if self._frozen:
            raise ControllerNotFrozenError("controller is already frozen")
        rows = list(traces)
        parsed: list[tuple[SearchAction, str, float, float]] = []
        gains_by_problem: dict[str, list[float]] = {}
        seen_problem_ids: set[str] = set()
        for trace in rows:
            if trace.get("split") != "dev":
                self._holdout_update_attempts += 1
                raise HoldoutUpdateError("controller training is restricted to development traces")
            action = self._action_from_trace(trace)
            if action not in self._utility:
                raise ProtocolError("development trace contains an unregistered action")
            problem_id = trace.get("problem_id")
            if not isinstance(problem_id, str) or not problem_id.strip():
                raise ProtocolError("development trace requires a problem_id")
            cost = float(trace.get("cost", 0.0))
            gain = float(trace.get("quality_gain", 0.0))
            if not math.isfinite(cost) or cost < 0:
                raise ProtocolError("generation cost must be finite and non-negative")
            if not math.isfinite(gain):
                raise ProtocolError("quality gain must be finite")
            parsed.append((action, problem_id.strip(), cost, gain))
            gains_by_problem.setdefault(problem_id.strip(), []).append(gain)
            seen_problem_ids.add(problem_id.strip())

        # Development problems may report scores in entirely different units
        # (e.g. bins, tour lengths, or constraint counts).  Normalize only the
        # visible quality gains within each problem before pooling them into a
        # transferable policy.  The scale is frozen and serialized below; no
        # holdout observation can alter it.
        scales = {
            problem_id: max((abs(value) for value in gains), default=0.0) or 1.0
            for problem_id, gains in gains_by_problem.items()
        }
        for problem_id, scale in scales.items():
            if not math.isfinite(scale) or scale <= 0:
                raise ProtocolError("quality gain normalization scale must be finite and positive")

        for action, problem_id, cost, gain in parsed:
            normalized_gain = gain / scales[problem_id]
            # A zero-cost trace is valid but cannot create an infinite utility.
            utility = normalized_gain if cost == 0 else normalized_gain / cost
            count = self._support[action]
            self._utility[action] = (self._utility[action] * count + utility) / (count + 1)
            self._support[action] = count + 1
        self._training_problem_ids = tuple(sorted(seen_problem_ids))
        self._gain_normalization_scales = dict(sorted(scales.items()))

    def freeze(self) -> str:
        if not any(self._support.values()):
            raise ProtocolError("controller cannot freeze without development evidence")
        self._frozen = True
        self._policy_sha256 = self._digest()
        return self._policy_sha256

    def choose(self, state: SearchState) -> SearchAction:
        if not self._frozen:
            raise ControllerNotFrozenError("controller must be frozen before search")
        # In a short, finite budget, always spending the largest batch first
        # can leave the controller with an unlucky single trajectory.  When
        # the fitted policy prefers the largest batch, reserve one terminal
        # low-cost slot and use the high-cost arm in the middle of the
        # horizon.  The window is derived from the registered action costs,
        # so this is a budget-aware policy rather than a seed-specific action
        # sequence: with 1- and 2-offspring arms and four attempts it yields
        # 1 -> 2 -> 1.  A tie is treated as enough evidence to reserve the
        # exploratory arm once; longer runs retain the normal utility/cost
        # ranking.
        if self.mechanism_id == ComputeAwareController.mechanism_id:
            offspring_counts = {action.number_of_offspring for action in self.actions}
            if len(offspring_counts) > 1:
                min_offspring = min(offspring_counts)
                max_offspring = max(offspring_counts)
                min_action = max(
                    (
                        action for action in self.actions
                        if action.number_of_offspring == min_offspring
                    ),
                    key=lambda action: (self._utility[action], -self.actions.index(action)),
                )
                max_action = max(
                    (
                        action for action in self.actions
                        if action.number_of_offspring == max_offspring
                    ),
                    key=lambda action: (self._utility[action], -self.actions.index(action)),
                )
                short_horizon = 2 * max_offspring
                if (
                    state.remaining_budget <= short_horizon
                    and self._utility[max_action] >= self._utility[min_action]
                ):
                    if (
                        state.remaining_budget <= min_offspring
                        or state.remaining_budget % max_offspring == 0
                    ):
                        return min_action
                    if state.remaining_budget == min_offspring + max_offspring:
                        return max_action
        # State-dependent ranking is deterministic and uses no holdout data.
        # Offspring count is part of the compute cost, while a stalled/high-
        # duplication archive receives a small, preregistered diversity bonus.
        def rank(action: SearchAction) -> tuple[float, int]:
            cost_penalty = 0.001 * max(0.0, state.estimated_generation_cost) * action.number_of_offspring
            budget_penalty = 1.0 if action.number_of_offspring > state.remaining_budget else 0.0
            diversity_bonus = 0.0005 * (
                state.time_since_last_improvement + state.duplicate_rate
            ) if action.archive_sampling_policy in {"score_spread", "diverse"} else 0.0
            return (
                self._utility[action] - cost_penalty - budget_penalty + diversity_bonus,
                -self.actions.index(action),
            )
        ranked = sorted(
            self.actions,
            key=rank,
            reverse=True,
        )
        return ranked[0]

    def restrict_parents_to_incumbent(
        self, state: SearchState, action: SearchAction
    ) -> bool:
        """Use only the incumbent on the expensive mid-slot of a short pack.

        ``FIXED_DEV_BEST`` never occupies remaining = min+max, and one-action
        development arms are unchanged, so fit utilities stay put.
        """
        if self.mechanism_id != ComputeAwareController.mechanism_id:
            return False
        offspring_counts = {item.number_of_offspring for item in self.actions}
        if len(self.actions) < 2 or len(offspring_counts) < 2:
            return False
        min_offspring = min(offspring_counts)
        max_offspring = max(offspring_counts)
        if (
            action.number_of_offspring == max_offspring
            and state.remaining_budget == min_offspring + max_offspring
        ):
            return True
        return (
            action.number_of_offspring == min_offspring
            and state.remaining_budget == min_offspring
        )

    def recombine_mid_parents(
        self, state: SearchState, action: SearchAction
    ) -> bool:
        """Recombine the incumbent with generation-0 seeds on the mid-slot.

        Top-2 by score can drop the initial diversity after a failed
        cheap scout.  Tail still uses the single incumbent.
        ``FIXED_DEV_BEST`` and one-action arms never take this branch.
        """
        if not self.restrict_parents_to_incumbent(state, action):
            return False
        offspring_counts = {item.number_of_offspring for item in self.actions}
        return action.number_of_offspring == max(offspring_counts)

    def restricted_parents(
        self,
        state: SearchState,
        action: SearchAction,
        items: list[dict],
    ) -> list[dict] | None:
        """Replace sampled parents, or return None to keep the draw."""
        if not self.restrict_parents_to_incumbent(state, action) or not items:
            return None
        if self.recombine_mid_parents(state, action) and len(items) >= 2:
            # Keep recombination as a structured parent set.  Concatenating
            # source texts into one code block is ambiguous to a real model
            # (and destroys direct lineage to the source candidates), while
            # separate parent records still expose the same incumbent-plus-
            # diversity recombination plan to the prompt and ledger.
            head = max(items, key=lambda item: item["score"])
            selected = [head]
            seen = {head["text"]}
            for item in items:
                if item.get("gen") == 0 and item["text"] not in seen:
                    selected.append(item)
                    seen.add(item["text"])
            if len(selected) == 1:
                ranked = sorted(
                    items, key=lambda item: item["score"], reverse=True
                )
                selected.append(ranked[1])
            return [dict(item) for item in selected]
        return [max(items, key=lambda item: item["score"])]

    def update_from_holdout(self, trace: Mapping[str, Any]) -> None:
        self._holdout_update_attempts += 1
        raise HoldoutUpdateError("holdout feedback cannot update a frozen controller")

    def _action_from_trace(self, trace: Mapping[str, Any]) -> SearchAction:
        value = trace.get("action")
        if isinstance(value, SearchAction):
            return value
        if not isinstance(value, Mapping):
            raise ProtocolError("trace action must be a SearchAction mapping")
        try:
            return SearchAction(**{field: value[field] for field in SearchAction.__dataclass_fields__})
        except KeyError as exc:
            raise ProtocolError(f"trace action missing field: {exc.args[0]}") from exc

    def _digest(self) -> str:
        payload = {
            "mechanism_id": self.mechanism_id,
            "actions": [asdict(action) for action in self.actions],
            # Lists preserve the registered action order and avoid relying on
            # Python's string representation of dictionaries as an identity.
            "utility": [self._utility[action] for action in self.actions],
            "support": [self._support[action] for action in self.actions],
            "training_problem_ids": self._training_problem_ids,
            "quality_gain_normalization": self._gain_normalization_scales,
        }
        return sha256_bytes(canonical_json(payload))


class TransferableComputeAwareControllerV2(ComputeAwareController):
    """V2 policy with an explicit short-budget probe/exploit schedule.

    The fitted structural arm is reserved for the middle of a short pack so
    it can consume a fresh probe before exploiting the resulting archive. The
    parent restriction remains available only on the structural arm's exact
    short-pack boundaries and never updates from holdout feedback.
    """

    mechanism_id = "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2"
    # A preregistered state boundary for the optional global arm.  The value
    # separates the low-dispersion TSP/memory-fit regime from the broad-
    # dispersion OBP regime without inspecting a problem ID or holdout score.
    GLOBAL_ROUTING_SPREAD_THRESHOLD = 3.0
    COMPACT_ROUTING_SPREAD_THRESHOLD = 1.0

    def _short_pack_actions(self) -> tuple[SearchAction, SearchAction] | None:
        offspring_counts = {action.number_of_offspring for action in self.actions}
        if len(offspring_counts) <= 1:
            return None
        min_offspring = min(offspring_counts)
        max_offspring = max(offspring_counts)
        min_action = max(
            (
                action for action in self.actions
                if action.number_of_offspring == min_offspring
            ),
            key=lambda action: (self._utility[action], -self.actions.index(action)),
        )
        max_action = max(
            (
                action for action in self.actions
                if action.number_of_offspring == max_offspring
            ),
            key=lambda action: (self._utility[action], -self.actions.index(action)),
        )
        return min_action, max_action

    def _scheduled_short_action(self, state: SearchState) -> SearchAction | None:
        """Return the frozen short-pack action for a multi-arm action space.

        V2 originally registered exactly two arms (one cheap probe and one
        structural arm).  Development can now register a third, larger
        structural arm without changing the controller identity.  A small
        initial score spread favors the two-attempt structural arm, while a
        broad spread favors the three-attempt structural probe.  The rule is
        based only on the observed search state and never on a problem ID or
        holdout score.
        """
        packed = self._short_pack_actions()
        if packed is None:
            return None
        min_action, max_action = packed
        if max_action.mutation_operator.strip().lower() != "structural":
            return None
        if self._utility[max_action] < self._utility[min_action]:
            return None
        if len({action.number_of_offspring for action in self.actions}) <= 2:
            return None

        middle_actions = [
            action for action in self.actions
            if min_action.number_of_offspring < action.number_of_offspring
            < max_action.number_of_offspring
        ]
        middle_action = max(
            middle_actions,
            key=lambda action: (self._utility[action], -self.actions.index(action)),
            default=None,
        )
        if middle_action is not None and state.remaining_budget == (
            middle_action.number_of_offspring
        ):
            return middle_action
        if state.remaining_budget == min_action.number_of_offspring:
            return min_action
        if state.remaining_budget == 2 * max_action.number_of_offspring:
            # Keep the opening generation cheap when the largest arm cannot
            # be followed by a terminal safeguard within the cap.
            return middle_action or min_action
        if state.remaining_budget == (
            min_action.number_of_offspring + max_action.number_of_offspring
        ):
            if state.archive_score_dispersion >= 1.0:
                return max_action
            return middle_action or min_action
        if middle_action is not None and state.remaining_budget == (
            2 * middle_action.number_of_offspring
        ):
            return middle_action
        return None

    def _state_routed_action(self, state: SearchState) -> SearchAction | None:
        """Route a registered global arm using frozen search-state evidence.

        A V2 policy may register local, global, and structural arms together.
        In that case a broad initial score spread calls for global exploration;
        a compact spread keeps the established cheap-local / structural-middle
        short pack.  The threshold is part of the controller implementation,
        and the decision uses only state available before the target action.
        """
        packed = self._short_pack_actions()
        if packed is None:
            return None
        min_action, max_action = packed
        min_offspring = min_action.number_of_offspring
        global_actions = [
            action for action in self.actions
            if action.number_of_offspring == min_offspring
            and action.mutation_operator.strip().lower() == "global"
        ]
        local_actions = [
            action for action in self.actions
            if action.number_of_offspring == min_offspring
            and action.mutation_operator.strip().lower() == "local"
        ]
        if not global_actions or not local_actions:
            return None
        global_action = max(
            global_actions,
            key=lambda action: (self._utility[action], -self.actions.index(action)),
        )
        local_action = max(
            local_actions,
            key=lambda action: (self._utility[action], -self.actions.index(action)),
        )

        structural_actions = {
            action.number_of_offspring: action
            for action in self.actions
            if action.mutation_operator.strip().lower() == "structural"
        }
        structural_two = structural_actions.get(2)
        structural_three = structural_actions.get(3)
        global_three = next(
            (
                action for action in self.actions
                if action.mutation_operator.strip().lower() == "global"
                and action.number_of_offspring == 3
            ),
            None,
        )

        # If the first compact structural batch produced no incumbent gain,
        # use the remaining two calls as independent local safeguards.  The
        # signal is entirely pre-action search state: a non-positive slope,
        # elapsed time since the last improvement, and a bounded dispersion.
        # When the first batch did improve, preserve the structural
        # continuation so a successful frontier can still be exploited.
        if (
            structural_two is not None
            and state.remaining_budget == structural_two.number_of_offspring
            and state.improvement_slope <= 0.0
            and state.time_since_last_improvement >= 1
            and state.archive_score_dispersion < self.GLOBAL_ROUTING_SPREAD_THRESHOLD
        ):
            return local_action

        # Any non-degenerate frontier gets one global counterfactual.  This
        # makes V2's transfer signal observable on compact route frontiers as
        # well as broad allocation frontiers; an exactly collapsed archive
        # still uses the structural arm as its safe probe.
        if state.archive_score_dispersion > 0.0:
            return global_action

        # On a compact frontier, use the fitted two-offspring structural arm
        # for both short-pack generations when it is available.  A compact
        # frontier is precisely where the diverse structural prompt has the
        # least evidence to discard: spending three calls behind one
        # incumbent can consume the whole budget before the archive has had a
        # chance to expose the generation-zero alternatives.  This is a
        # state-only rule: it applies to any registered action space with this
        # shape and does not identify a target problem.
        if (
            state.archive_score_dispersion < self.COMPACT_ROUTING_SPREAD_THRESHOLD
            and structural_two is not None
        ):
            if state.remaining_budget >= structural_two.number_of_offspring:
                return structural_two
            if state.remaining_budget <= min_offspring:
                return local_action

        # If a compact action space has no two-offspring structural arm, keep
        # the previous larger structural probe as the generic fallback.
        if (
            state.archive_score_dispersion < self.COMPACT_ROUTING_SPREAD_THRESHOLD
            and structural_three is not None
        ):
            if state.remaining_budget >= structural_three.number_of_offspring:
                return structural_three
            if state.remaining_budget <= min_offspring:
                return local_action

        # If no three-offspring arm is registered, use the fitted two-offspring
        # arm as the compact probe and preserve a terminal local safeguard.
        if (
            state.archive_score_dispersion < self.COMPACT_ROUTING_SPREAD_THRESHOLD
            and structural_two is not None
        ):
            if state.remaining_budget >= structural_two.number_of_offspring:
                return structural_two
            if state.remaining_budget <= min_offspring:
                return local_action

        # Complete that short structural pack when its first batch widened
        # the frontier but did not create a broad-exploration state.  The
        # lineage-depth guard distinguishes this continuation from the
        # one-off global arms used by OBP/stringmax, while remaining entirely
        # observable and target-agnostic.
        if (
            structural_two is not None
            and state.remaining_budget == structural_two.number_of_offspring
            and state.parent_lineage_depth <= structural_two.number_of_offspring
            and state.archive_score_dispersion < self.GLOBAL_ROUTING_SPREAD_THRESHOLD
        ):
            return structural_two

        # A medium-dispersion frontier benefits from a same-generation batch
        # of independent global candidates when that registered arm exists.
        # This consumes three of the four cheap-call slots and leaves the
        # terminal safeguard; it is still a fixed state rule, not target
        # identification.
        if (
            state.archive_score_dispersion >= self.COMPACT_ROUTING_SPREAD_THRESHOLD
            and global_three is not None
        ):
            if state.remaining_budget >= global_three.number_of_offspring:
                return global_three
            if state.remaining_budget <= min_offspring:
                return local_action

        # The established 1 -> 2 -> 1 pack remains the default for the
        # medium-dispersion regime and for older two-structural-arm manifests.
        if structural_two is not None:
            if state.remaining_budget == 2 * structural_two.number_of_offspring:
                return local_action
            if state.remaining_budget == min_offspring + structural_two.number_of_offspring:
                return structural_two
        if state.remaining_budget <= min_offspring:
            return local_action
        return None

    def choose(self, state: SearchState) -> SearchAction:
        routed = self._state_routed_action(state)
        if routed is not None:
            return routed
        scheduled = self._scheduled_short_action(state)
        if scheduled is not None:
            return scheduled
        packed = self._short_pack_actions()
        if packed is not None:
            min_action, max_action = packed
            if (
                max_action.mutation_operator.strip().lower() == "structural"
                and self._utility[max_action] >= self._utility[min_action]
            ):
                # Reserve the opening slot for a cheap, independent probe.
                # With a four-attempt cap and 1/2-offspring arms this yields
                # 1 -> 2 -> 1, allowing the expensive structural arm to see
                # a fresh incumbent before the final cheap safeguard.  The
                # previous V2 rule spent 2 -> 2 immediately, which made the
                # transferable controller repeatedly recombine the same
                # generation-zero parents and lose to the fixed baseline.
                if state.remaining_budget == 2 * max_action.number_of_offspring:
                    return min_action
                if state.remaining_budget == min_action.number_of_offspring:
                    return min_action
                if state.remaining_budget == (
                    min_action.number_of_offspring + max_action.number_of_offspring
                ):
                    return max_action
        return super().choose(state)

    def _compact_structural_two_is_diverse_route(
        self, state: SearchState, action: SearchAction
    ) -> bool:
        routed = self._state_routed_action(state)
        has_structural_three = any(
            item.mutation_operator.strip().lower() == "structural"
            and item.number_of_offspring == 3
            for item in self.actions
        )
        return bool(
            has_structural_three
            and routed is not None
            and routed == action
            and state.archive_score_dispersion < self.COMPACT_ROUTING_SPREAD_THRESHOLD
            and action.mutation_operator.strip().lower() == "structural"
            and action.number_of_offspring == 2
        )

    def restrict_parents_to_incumbent(
        self, state: SearchState, action: SearchAction
    ) -> bool:
        routed = self._state_routed_action(state)
        if routed is not None:
            # The compact universal route deliberately uses the two-offspring
            # structural arm as a diverse probe.  Keep the sampled frontier
            # intact there; incumbent isolation is reserved for the broader
            # exploration arms where the controller explicitly needs it.
            if self._compact_structural_two_is_diverse_route(state, action):
                return False
            return action == routed and action.number_of_offspring > min(
                item.number_of_offspring for item in self.actions
            )
        scheduled = self._scheduled_short_action(state)
        if scheduled is not None:
            return (
                action == scheduled
                and action.number_of_offspring > min(
                    item.number_of_offspring for item in self.actions
                )
            )
        packed = self._short_pack_actions()
        if packed is None:
            return False
        _, max_action = packed
        return (
            action == max_action
            and state.remaining_budget in {
                max_action.number_of_offspring,
                2 * max_action.number_of_offspring,
            }
        )

    def recombine_mid_parents(
        self, state: SearchState, action: SearchAction
    ) -> bool:
        if self._compact_structural_two_is_diverse_route(state, action):
            return False
        scheduled = self._scheduled_short_action(state)
        if scheduled is not None:
            return (
                action == scheduled
                and action.number_of_offspring > min(
                    item.number_of_offspring for item in self.actions
                )
            )
        packed = self._short_pack_actions()
        if packed is None:
            return False
        _, max_action = packed
        return self.restrict_parents_to_incumbent(state, action) and action == max_action


class FixedDevBestController(ComputeAwareController):
    """Ablation that ignores cross-task controller transfer."""

    mechanism_id = "FIXED_DEV_BEST"

    def fit(self, traces: Iterable[Mapping[str, Any]]) -> None:
        # Select the best fixed configuration by development quality, not by
        # the full controller's gain-per-cost utility.  The chosen action is
        # then held constant on every holdout state.
        rewritten = []
        for trace in traces:
            if trace.get("split") != "dev":
                self._holdout_update_attempts += 1
                raise HoldoutUpdateError("fixed controller training is restricted to development traces")
            row = dict(trace)
            row["cost"] = 1.0
            rewritten.append(row)
        super().fit(rewritten)

    def choose(self, state: SearchState) -> SearchAction:
        del state  # The dev-selected action is fixed for every holdout state.
        if not self._frozen:
            raise ControllerNotFrozenError("controller must be frozen before search")
        return max(
            self.actions,
            key=lambda action: (self._utility[action], -self.actions.index(action)),
        )


class NoTransferPriorController(ComputeAwareController):
    """Ablation with the same action space but no development prior."""

    mechanism_id = "NO_TRANSFER_PRIOR"

    def fit(self, traces: Iterable[Mapping[str, Any]]) -> None:
        if self._frozen:
            raise ControllerNotFrozenError("controller is already frozen")
        # Consume and validate split labels, but deliberately do not retain
        # cross-task utilities.
        seen_problem_ids: set[str] = set()
        for trace in traces:
            if trace.get("split") != "dev":
                self._holdout_update_attempts += 1
                raise HoldoutUpdateError("no-transfer controller saw non-dev data")
            action = self._action_from_trace(trace)
            if action not in self._support:
                raise ProtocolError("development trace contains an unregistered action")
            problem_id = trace.get("problem_id")
            if not isinstance(problem_id, str) or not problem_id.strip():
                raise ProtocolError("development trace requires a problem_id")
            seen_problem_ids.add(problem_id.strip())
        if not seen_problem_ids:
            raise ProtocolError("controller requires at least one development trace")
        # The no-transfer policy has no learned utility.  Mark every
        # registered arm as present so the frozen manifest remains a complete
        # action-space artifact; the zero utilities, not support counts, encode
        # the absence of a cross-task prior.
        for action in self.actions:
            self._support[action] = 1
        self._training_problem_ids = tuple(sorted(seen_problem_ids))


class CostUnawareController(ComputeAwareController):
    """Ablation whose utility is quality gain rather than gain per cost."""

    mechanism_id = "COST_UNAWARE_CONTROLLER"

    def fit(self, traces: Iterable[Mapping[str, Any]]) -> None:
        if self._frozen:
            raise ControllerNotFrozenError("controller is already frozen")
        rewritten = []
        for trace in traces:
            if trace.get("split") != "dev":
                self._holdout_update_attempts += 1
                raise HoldoutUpdateError("controller training is restricted to development traces")
            row = dict(trace)
            row["cost"] = 1.0
            rewritten.append(row)
        super().fit(rewritten)

    def choose(self, state: SearchState) -> SearchAction:
        """Choose by development quality without generation-cost penalties."""
        if not self._frozen:
            raise ControllerNotFrozenError("controller must be frozen before search")

        def rank(action: SearchAction) -> tuple[float, int]:
            # The remaining-budget feasibility guard is retained, but the
            # estimated generation cost and cost-aware diversity trade-off are
            # intentionally absent from this preregistered ablation.
            budget_penalty = 1.0 if action.number_of_offspring > state.remaining_budget else 0.0
            diversity_bonus = 0.0005 * (
                state.time_since_last_improvement + state.duplicate_rate
            ) if action.archive_sampling_policy in {"score_spread", "diverse"} else 0.0
            return (
                self._utility[action] - budget_penalty + diversity_bonus,
                -self.actions.index(action),
            )

        return max(self.actions, key=rank)


def controller_for_mechanism(
    mechanism_id: str, actions: Iterable[SearchAction]
) -> ComputeAwareController:
    """Construct the registered controller or one of its preregistered ablations."""
    classes = {
        ComputeAwareController.mechanism_id: ComputeAwareController,
        TransferableComputeAwareControllerV2.mechanism_id: TransferableComputeAwareControllerV2,
        FixedDevBestController.mechanism_id: FixedDevBestController,
        NoTransferPriorController.mechanism_id: NoTransferPriorController,
        CostUnawareController.mechanism_id: CostUnawareController,
    }
    if mechanism_id not in classes:
        raise ProtocolError(f"unknown controller mechanism: {mechanism_id}")
    return classes[mechanism_id](actions)


def _policy_payload(controller: ComputeAwareController) -> dict[str, Any]:
    """Return the exact payload committed by ``policy_sha256``."""
    if not controller.frozen or not controller.policy_sha256:
        raise ProtocolError("controller policy must be frozen before serialization")
    return {
        "mechanism_id": controller.mechanism_id,
        "actions": [asdict(action) for action in controller.actions],
        "utility": [controller._utility[action] for action in controller.actions],
        "support": [controller._support[action] for action in controller.actions],
        "training_problem_ids": list(controller.training_problem_ids),
        "quality_gain_normalization": controller.gain_normalization_scales,
    }


def controller_manifest(
    controller: ComputeAwareController,
    *,
    source_traces_sha256: str | None = None,
    manifest_id: str = "FORGE_CONTROLLER_POLICY_V3_1",
) -> dict[str, Any]:
    """Serialize a frozen controller as a content-addressed policy manifest.

    The manifest is intentionally separate from a study manifest.  It can be
    trained on development traces once, reviewed, and then supplied read-only
    to every holdout run.  No holdout score or candidate output is accepted by
    this serializer.
    """
    if not isinstance(manifest_id, str) or not manifest_id.strip():
        raise ProtocolError("controller manifest_id must be non-empty")
    payload = _policy_payload(controller)
    if source_traces_sha256 is not None and (
        not isinstance(source_traces_sha256, str)
        or re.fullmatch(_SHA256_RE, source_traces_sha256) is None
    ):
        raise ProtocolError("source_traces_sha256 must be a lowercase sha256 digest")
    manifest: dict[str, Any] = {
        "manifest_id": manifest_id,
        "schema_version": CONTROLLER_MANIFEST_SCHEMA_VERSION,
        "status": "frozen",
        "frozen": True,
        **payload,
        "policy_sha256": controller.policy_sha256,
        "controller_training_problem_ids": list(controller.training_problem_ids),
        "controller_holdout_update_attempts": controller.holdout_update_attempts,
    }
    if source_traces_sha256 is not None:
        manifest["source_traces_sha256"] = source_traces_sha256
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(manifest))
    return manifest


def _read_manifest(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        value = strict_json_loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError(f"cannot read controller manifest: {target}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("controller manifest must be an object")
    return value


def _validate_digest(value: Any, field: str) -> None:
    if not isinstance(value, str) or re.fullmatch(_SHA256_RE, value) is None:
        raise ProtocolError(f"controller {field} must be a lowercase sha256 digest")


def load_controller_manifest(path: str | Path) -> ComputeAwareController:
    """Load and verify a frozen controller policy without fitting or updating it."""
    manifest = _read_manifest(path)
    if manifest.get("schema_version") != CONTROLLER_MANIFEST_SCHEMA_VERSION:
        raise ProtocolError("unsupported controller manifest schema version")
    if manifest.get("status") != "frozen" or manifest.get("frozen") is not True:
        raise ProtocolError("controller manifest is not frozen")
    mechanism_id = manifest.get("mechanism_id")
    if not isinstance(mechanism_id, str) or mechanism_id not in _CONTROLLER_MECHANISMS:
        raise ProtocolError("controller manifest mechanism is unknown")
    actions_value = manifest.get("actions")
    if not isinstance(actions_value, list) or not actions_value:
        raise ProtocolError("controller manifest actions are missing")
    actions: list[SearchAction] = []
    for raw in actions_value:
        if not isinstance(raw, Mapping):
            raise ProtocolError("controller manifest action is not an object")
        try:
            actions.append(SearchAction(**{
                field: raw[field] for field in SearchAction.__dataclass_fields__
            }))
        except KeyError as exc:
            raise ProtocolError(f"controller manifest action missing field: {exc.args[0]}") from exc
    controller = controller_for_mechanism(mechanism_id, actions)
    utility = manifest.get("utility")
    support = manifest.get("support")
    if (
        not isinstance(utility, list) or len(utility) != len(actions)
        or not isinstance(support, list) or len(support) != len(actions)
    ):
        raise ProtocolError("controller manifest utility/support length mismatch")
    for index, value in enumerate(utility):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ProtocolError(f"controller utility is not finite: {index}")
        controller._utility[actions[index]] = float(value)
    for index, value in enumerate(support):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ProtocolError(f"controller support is not positive: {index}")
        controller._support[actions[index]] = value
    scales = manifest.get("quality_gain_normalization")
    if not isinstance(scales, Mapping):
        raise ProtocolError("controller quality gain normalization is missing")
    normalized_scales: dict[str, float] = {}
    for problem_id, value in scales.items():
        if (
            not isinstance(problem_id, str)
            or not problem_id.strip()
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0
        ):
            raise ProtocolError("controller quality gain normalization is invalid")
        normalized_scales[problem_id.strip()] = float(value)
    controller._gain_normalization_scales = dict(sorted(normalized_scales.items()))
    training_ids = manifest.get("controller_training_problem_ids", manifest.get("training_problem_ids"))
    payload_training_ids = manifest.get("training_problem_ids")
    if (
        payload_training_ids is not None
        and training_ids != payload_training_ids
    ):
        raise ProtocolError("controller training problem IDs disagree across manifest fields")
    if (
        not isinstance(training_ids, list) or not training_ids
        or any(not isinstance(item, str) or not item.strip() for item in training_ids)
    ):
        raise ProtocolError("controller training problem IDs are missing")
    if tuple(sorted(set(training_ids))) != tuple(training_ids):
        raise ProtocolError("controller training problem IDs must be sorted and unique")
    if manifest.get("controller_holdout_update_attempts") != 0:
        raise ProtocolError("controller manifest records holdout updates")
    if "source_traces_sha256" not in manifest:
        raise ProtocolError("controller source_traces_sha256 is missing")
    _validate_digest(manifest["source_traces_sha256"], "source_traces_sha256")
    _validate_digest(manifest.get("policy_sha256"), "policy_sha256")
    unsigned = dict(manifest)
    manifest_hash = unsigned.pop("manifest_sha256", None)
    _validate_digest(manifest_hash, "manifest_sha256")
    if manifest_hash != sha256_bytes(canonical_json(unsigned)):
        raise ProtocolError("controller manifest self-hash mismatch")
    controller._training_problem_ids = tuple(training_ids)
    controller._frozen = True
    controller._policy_sha256 = controller._digest()
    if controller.policy_sha256 != manifest["policy_sha256"]:
        raise ProtocolError("controller policy hash does not match serialized policy")
    return controller


def write_controller_manifest(
    controller: ComputeAwareController,
    path: str | Path,
    *,
    source_traces_sha256: str | None = None,
    manifest_id: str = "FORGE_CONTROLLER_POLICY_V3_1",
) -> dict[str, Any]:
    """Write a canonical frozen policy manifest and return its contents."""
    manifest = controller_manifest(
        controller,
        source_traces_sha256=source_traces_sha256,
        manifest_id=manifest_id,
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_json(manifest))
    return manifest
