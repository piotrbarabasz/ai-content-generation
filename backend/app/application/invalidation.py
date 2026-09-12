"""Derive editorial freshness without scheduling, publishing or changing selections."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from app.domain.base import DomainValidationError
from app.domain.dependencies import ArtifactDependency, FailedAttempt, InputEdge, Provenance, RequestFingerprint


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class FreshnessResult:
    output_key: str
    state: Freshness
    selected_artifact_id: str | None
    reasons: tuple[str, ...] = ()
    review_required: bool = False
    failed_attempts: tuple[FailedAttempt, ...] = ()


def evaluate_freshness(*, requests: Mapping[str, RequestFingerprint], sources: Mapping[str, str],
                       selected: Mapping[str, str], artifacts: Mapping[str, ArtifactDependency],
                       failed_attempts: Sequence[FailedAttempt] = ()) -> dict[str, FreshnessResult]:
    """Compare consumed requests with current bindings and propagate actual edges.

    `requests` supplies current algorithms/settings and input bindings. Source-edge
    fingerprints and artifact IDs are rebound from the current snapshots below.
    Manual outputs remain owned selections: incompatibility flags review, while
    downstream compatibility follows their unchanged selected bytes.
    """
    requests, sources, selected, artifacts = map(dict, (requests, sources, selected, artifacts))
    failed_attempts = tuple(failed_attempts)
    results, visiting = {}, set()

    def visit(key):
        if key in visiting:
            raise DomainValidationError("Artifact dependency cycle detected.")
        if key in results:
            return results[key]
        if key not in requests:
            raise DomainValidationError(f"Missing current request for dependency: {key}")
        visiting.add(key)
        request = requests[key]
        bound, reasons = [], []
        for edge in request.inputs:
            if edge.artifact_id is None:
                current = sources.get(edge.key)
                if current is None:
                    reasons.append(f"missing input: {edge.name}")
                else:
                    bound.append(replace(edge, fingerprint=current))
            else:
                upstream = visit(edge.key)
                dependency = artifacts.get(selected.get(edge.key))
                if dependency is None:
                    reasons.append(f"missing input: {edge.name}")
                else:
                    bound.append(InputEdge.artifact(edge.name, edge.key, dependency.artifact_id, dependency.checksum))
                    if upstream.state != Freshness.FRESH and dependency.declaration.provenance != Provenance.MANUAL:
                        reasons.append(f"stale input: {edge.name}")
        artifact_id = selected.get(key)
        artifact = artifacts.get(artifact_id)
        manual = False
        if artifact is None:
            state = Freshness.MISSING
        else:
            if artifact.artifact_id != artifact_id or artifact.declaration.output_key != key:
                raise DomainValidationError("Selected artifact does not belong to this output binding.")
            expected = replace(request, inputs=tuple(bound))
            if artifact.declaration.request.fingerprint != expected.fingerprint:
                reasons.append("request changed")
            state = Freshness.STALE if reasons else Freshness.FRESH
            manual = artifact.declaration.provenance == Provenance.MANUAL
        result = FreshnessResult(key, state, artifact_id, tuple(reasons),
                                 manual and state == Freshness.STALE,
                                 tuple(attempt for attempt in failed_attempts if attempt.output_key == key))
        visiting.remove(key)
        results[key] = result
        return result

    for key in sorted(requests):
        visit(key)
    return results
