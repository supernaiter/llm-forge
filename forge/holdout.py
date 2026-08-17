"""Search/holdout information barrier primitives.

The object intentionally exposes no hidden instance or score through its
search view.  Hidden evaluation requires a verifier-owned capability that is
not serializable and is only issued during the final unblinding phase.
"""
from __future__ import annotations

import hashlib
import math
import secrets
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping


class HiddenTestAccessError(PermissionError):
    """Raised when search-side code requests hidden data or scores."""


class VerifierAuthority:
    """Verifier-owned authority required for final unblinding.

    Search code should receive only :attr:`SealedHoldout.search_view`.  The
    authority object stays in the external verifier process and is the only
    object allowed to mint a one-shot capability.  This is an explicit
    capability boundary; it is not presented as a substitute for process
    isolation against a fully compromised Python interpreter.
    """

    __slots__ = ("_secret",)

    def __init__(self) -> None:
        self._secret = object()

    def issue_unblinding_capability(self, holdout: "SealedHoldout") -> str:
        if not isinstance(holdout, SealedHoldout) or holdout._authority is not self:
            raise HiddenTestAccessError("authority is not bound to this holdout")
        return holdout._issue_unblinding_capability(self)


@dataclass(frozen=True)
class SearchProblemView:
    problem_id: str
    family: str
    distribution: str
    search_instance_count: int
    hidden_instance_count: int


class SealedHoldout:
    """Hold hidden instances outside the search-facing object graph."""

    def __init__(self, *, problem_id: str, family: str, distribution: str,
                 hidden_instances: Iterable[Any],
                 authority: VerifierAuthority | None = None):
        self._problem_id = problem_id
        self._family = family
        self._distribution = distribution
        self._hidden_instances = tuple(hidden_instances)
        self._authority = authority
        self._phase = "sealed"
        self._capability: str | None = None
        self._violations = 0

    @property
    def search_view(self) -> SearchProblemView:
        return SearchProblemView(
            problem_id=self._problem_id,
            family=self._family,
            distribution=self._distribution,
            search_instance_count=0,
            # Even the hidden cardinality can be a side channel (for example,
            # when a generator adapts effort to test-set size).  The sealed
            # search view exposes no hidden-content-derived count.
            hidden_instance_count=0,
        )

    @property
    def hidden_access_violation_count(self) -> int:
        return self._violations

    def hidden_instances(self) -> tuple[Any, ...]:
        self._violations += 1
        raise HiddenTestAccessError("hidden instances are unavailable before unblinding")

    def hidden_scores(self, candidate: Any) -> tuple[float, ...]:
        self._violations += 1
        raise HiddenTestAccessError("hidden scores are unavailable before unblinding")

    def issue_unblinding_capability(
        self, *, authority: VerifierAuthority | None = None
    ) -> str:
        """Issue only when called with the verifier authority bound at init."""
        if authority is None:
            raise HiddenTestAccessError("unblinding requires verifier authority")
        if authority is not self._authority:
            raise HiddenTestAccessError("invalid verifier authority")
        return self._issue_unblinding_capability(authority)

    def _issue_unblinding_capability(self, authority: VerifierAuthority) -> str:
        if authority is not self._authority:
            raise HiddenTestAccessError("invalid verifier authority")
        if self._phase != "sealed":
            raise HiddenTestAccessError("unblinding capability already issued")
        self._capability = secrets.token_urlsafe(32)
        self._phase = "unblinded"
        return self._capability

    def evaluate_after_unblinding(self, capability: str, candidate: Any,
                                  scorer: Callable[[Any, Any], float]) -> tuple[float, ...]:
        if self._phase != "unblinded" or capability != self._capability:
            self._violations += 1
            raise HiddenTestAccessError("invalid or premature unblinding capability")
        scores: list[float] = []
        for instance in self._hidden_instances:
            try:
                score = float(scorer(candidate, instance))
            except Exception as exc:
                self._violations += 1
                raise HiddenTestAccessError("hidden evaluator failed") from exc
            if not math.isfinite(score):
                self._violations += 1
                raise HiddenTestAccessError("hidden evaluator returned a non-finite score")
            scores.append(score)
        return tuple(scores)

    def audit(self) -> dict[str, Any]:
        return {
            "problem_id": self._problem_id,
            "distribution": self._distribution,
            "hidden_instance_count": 0,
            "phase": self._phase,
            "hidden_test_side_channel_count": self._violations,
            "search_side_scores_exposed": False,
        }


def hidden_content_digest(instances: Iterable[Any]) -> str:
    """Hash hidden content for verifier-local bookkeeping only.

    Callers must never put this digest into a search prompt or search-side
    event; it exists for an external verifier's private manifest.
    """
    values = "\n".join(repr(item) for item in instances).encode("utf-8")
    return hashlib.sha256(values).hexdigest()
