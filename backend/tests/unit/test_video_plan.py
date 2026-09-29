import pytest

from app.domain.base import DomainValidationError
from app.domain.video_plan import (PlannedSection, VideoFormat, VideoPlanGroup,
                                   VideoPlanRevision)


def social(duration=45):
    kinds = ["hook", "body", "explanation", "close"]
    budget = [duration // 4 + (1 if i < duration % 4 else 0) for i in range(4)]
    sections = [PlannedSection(f"s{i}", f"Section {i}", kind, "Purpose", seconds,
                               round(seconds * 2.2), max(1, round(seconds / 6)))
                for i, (kind, seconds) in enumerate(zip(kinds, budget))]
    groups = tuple(VideoPlanGroup(f"g{i}", kind, kind.title(), "Purpose", section.target_duration_seconds, (section,))
                   for i, (kind, section) in enumerate(zip(kinds, sections)))
    return VideoPlanRevision("p1", "project1", "en", VideoFormat.SOCIAL, duration,
                             "A topic", "Working title", "Brief", "Style", groups)


def standard(duration=600):
    kinds = ["cold_open", "introduction", "chapter", "chapter", "chapter", "chapter", "chapter", "conclusion"]
    budgets = [duration // len(kinds) + (1 if i < duration % len(kinds) else 0) for i in range(len(kinds))]
    groups = []
    for i, (kind, seconds) in enumerate(zip(kinds, budgets)):
        parts = [seconds] if kind != "chapter" else [seconds // 2, seconds - seconds // 2]
        sections = tuple(PlannedSection(f"ss{i}-{j}", f"Section {i}-{j}", kind, "Purpose", part,
                                        round(part * 2.2), max(1, round(part / 10)))
                         for j, part in enumerate(parts))
        groups.append(VideoPlanGroup(f"sg{i}", kind, kind.title(), "Purpose", seconds, sections))
    return VideoPlanRevision("sp1", "project1", "en", VideoFormat.STANDARD, duration,
                             "A topic", "Working title", "Brief", "Style", tuple(groups))


@pytest.mark.parametrize("duration", [30, 45, 60])
def test_social_duration_profiles_and_round_trip(duration):
    plan = social(duration)
    assert sum(g.target_duration_seconds for g in plan.groups) == duration
    assert VideoPlanRevision.from_payload(plan.to_payload()) == plan


@pytest.mark.parametrize("duration", [29, 61])
def test_social_rejects_out_of_profile_duration(duration):
    with pytest.raises(DomainValidationError):
        social(duration)


@pytest.mark.parametrize("duration", [540, 600, 660])
def test_standard_duration_profiles(duration):
    plan = standard(duration)
    assert sum(g.target_duration_seconds for g in plan.groups) == duration
    assert sum(s.target_duration_seconds for g in plan.groups for s in g.sections) == duration


@pytest.mark.parametrize("duration", [539, 661])
def test_standard_rejects_out_of_profile_duration(duration):
    with pytest.raises(DomainValidationError):
        standard(duration)


def test_strict_payload_duplicate_identity_and_tuple_snapshot():
    source = [social().groups[0]]
    with pytest.raises(DomainValidationError):
        VideoPlanRevision("p1", "project1", "en", "social", 45, "Topic", "Title", "Brief", "Style", source)
    plan = social()
    payload = plan.to_payload()
    payload["unexpected"] = True
    with pytest.raises(DomainValidationError):
        VideoPlanRevision.from_payload(payload)
    with pytest.raises(DomainValidationError):
        VideoPlanRevision.from_payload({**plan.to_payload(), "version": 2})
    assert isinstance(plan.groups, tuple) and isinstance(plan.groups[0].sections, tuple)
    with pytest.raises((AttributeError, TypeError)):
        plan.topic = "mutated"


def test_list_inputs_are_frozen_and_duplicate_ids_and_bad_sums_rejected():
    plan = social()
    groups = list(plan.groups)
    copied = VideoPlanRevision("p2", "project1", "en", "social", 45, "Topic", "Title", "Brief", "Style", groups)
    groups.clear()
    assert len(copied.groups) == 4
    duplicated = list(plan.groups)
    duplicated[-1] = VideoPlanGroup("g0", "close", "Close", "Purpose",
                                   duplicated[-1].target_duration_seconds,
                                   (PlannedSection("s0", "Close", "close", "Purpose",
                                                   duplicated[-1].target_duration_seconds, 10, 1),))
    with pytest.raises(DomainValidationError, match="identities"):
        VideoPlanRevision("p2", "project1", "en", "social", 45, "Topic", "Title", "Brief", "Style", duplicated)
    with pytest.raises(DomainValidationError, match="sum"):
        VideoPlanGroup("bad", "body", "Body", "Purpose", 2,
                       (PlannedSection("s", "Section", "body", "Purpose", 1, 2, 1),))
