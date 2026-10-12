"""Pure chapter/metadata contracts, including reordered and trimmed narrative groups."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace as NS

import pytest

from app.domain.caption_track import validate_caption_segments
from app.domain.plan_script import planned_section_identity
from app.domain.publishing_package import (CompositionTiming, METADATA_SCHEMA, parse_chapters,
    chapter_text, timeline_chapters, validate_chapters, validate_metadata)
from app.domain.timeline import TimelineRevision
from tests.unit.test_timeline import compile_values, media


def metadata():
    return {"titles": {"curiosity": "What happens when the web goes dark?", "search": "A world without internet", "cinematic": "The silent network"},
            "description": "Explore life without internet.", "keywords": ["internet"],
            "hashtags": ["#Internet", "#Documentary", "#Technology"], "pinned_comment": "What would you do?"}


def mapping(order=(0, 1, 2), lengths=(20, 20, 20)):
    groups = [NS(id=f"group{i}", title=f"Topic {i}", sections=[NS(id=f"planned{i}")]) for i in range(3)]
    sections, clips = [], []
    for i in order:
        value = media(str(i), end=lengths[i] * 8000, total=30 * 8000)
        section_id = planned_section_identity("project", f"planned{i}")
        value = replace(value, section_id=section_id)
        clips.append(value); sections.append(NS(section_id=section_id, id=value.section_revision_id))
    timeline = compile_values(*clips)
    plan = NS(project_id="project", id="plan", groups=groups)
    binding = NS(video_plan_revision_id="plan", script_id="script")
    script = NS(script_id="script", sections=sections)
    return plan, binding, script, timeline


def test_exact_timeline_chapters_ignore_planned_duration_and_follow_reordering_trims():
    plan, binding, script, timeline = mapping(order=(2, 0, 1), lengths=(12, 21, 15))
    timing = CompositionTiming(timeline.duration, timeline.duration)
    result = timeline_chapters(plan, binding, script, timeline, timing)
    assert result == [{"seconds": 0, "title": "Topic 2"}, {"seconds": 15, "title": "Topic 0"}, {"seconds": 27, "title": "Topic 1"}]
    assert parse_chapters(chapter_text(result)) == result


def test_branding_before_and_after_cold_open_do_not_create_a_short_intro_chapter():
    plan, binding, script, timeline = mapping()
    for insertion, expected in ((0, [0, 23, 43]), (20, [0, 23, 43])):
        timing = CompositionTiming(60, 73, intro=3, insertion=insertion, outro=10)
        assert [c["seconds"] for c in timeline_chapters(plan, binding, script, timeline, timing)] == expected


@pytest.mark.parametrize("chapters,duration", [
    ([{"seconds": 0, "title": "A"}], 60),
    ([{"seconds": 1, "title": "A"}, {"seconds": 20, "title": "B"}, {"seconds": 40, "title": "C"}], 60),
    ([{"seconds": 0, "title": "A"}, {"seconds": 9, "title": "B"}, {"seconds": 40, "title": "C"}], 60),
    ([{"seconds": 0, "title": "A"}, {"seconds": 20, "title": "B"}, {"seconds": 20, "title": "C"}], 60),
    ([{"seconds": 0, "title": "A"}, {"seconds": 20, "title": "A!"}, {"seconds": 40, "title": "C"}], 60),
    ([{"seconds": 0, "title": "A"}, {"seconds": 20, "title": "B"}, {"seconds": 52, "title": "C"}], 60),
    ([{"seconds": 0, "title": "A"}, {"seconds": 20, "title": "B"}, {"seconds": 60, "title": "C"}], 60),
])
def test_invalid_chapter_lists_require_review(chapters, duration):
    with pytest.raises(ValueError): validate_chapters(chapters, duration)


def test_stale_or_split_group_mapping_blocks_chapters():
    plan, binding, script, timeline = mapping()
    script.sections[0].id = "stale"
    with pytest.raises(ValueError, match="stale"): timeline_chapters(plan, binding, script, timeline, CompositionTiming(60, 60))
    plan, binding, script, timeline = mapping()
    plan.groups[2] = NS(id="group0", title="Topic 0", sections=[NS(id="planned2")])
    with pytest.raises(ValueError, match="split/repeated"): timeline_chapters(plan, binding, script, timeline, CompositionTiming(60, 60))


def test_srt_piecewise_offsets_leave_cards_empty_and_preserve_original_segments():
    segments = validate_caption_segments([{"start_ms": 0, "end_ms": 1800, "text": "Cold open"},
                                         {"start_ms": 2000, "end_ms": 4000, "text": "Later narration"}])
    result = CompositionTiming(5, 18, intro=3, insertion=2, outro=10).captions(segments)
    assert [(s.start_ms, s.end_ms) for s in result] == [(0, 1800), (5000, 7000)]
    assert segments[1].start_ms == 2000
    with pytest.raises(ValueError, match="crosses"):
        CompositionTiming(5, 8, intro=3, insertion=1).captions(segments)


def test_strict_metadata_schema_and_guardrails():
    assert METADATA_SCHEMA["additionalProperties"] is False
    assert set(METADATA_SCHEMA["properties"]["titles"]["properties"]) == {"curiosity", "search", "cinematic"}
    assert validate_metadata(metadata()) == metadata()
    for edit in (lambda m: m.update(unexpected=True), lambda m: m["titles"].update(search="x" * 101),
                 lambda m: m.update(description="Visit https://invented.example"), lambda m: m.update(description="97% of people agree")):
        value = deepcopy(metadata()); edit(value)
        with pytest.raises(ValueError): validate_metadata(value, generated_context="Known internet facts")


def test_explicit_configured_links_allowed_and_numbers_in_context_are_not_fabricated():
    value = metadata(); value["description"] = "24 hours. https://channel.example"
    validate_metadata(value, allowed_urls=["https://channel.example"], generated_context="24 hours")
    with pytest.raises(ValueError): validate_metadata(value)


def test_llm_timestamps_are_rejected_and_unicode_srt_is_preserved():
    value = metadata(); value["description"] = "00:00 First chapter"
    with pytest.raises(ValueError, match="timestamps"): validate_metadata(value, generated_context="00:00")
    from app.domain.caption_track import serialize_srt
    segments = validate_caption_segments([{"start_ms": 0, "end_ms": 1000, "text": "Zażółć gęślą jaźń"}])
    assert "Zażółć gęślą jaźń" in serialize_srt(CompositionTiming(2, 5, intro=3).captions(segments)).encode("utf-8").decode("utf-8")


def test_platform_metadata_limits_include_utf8_bytes_tags_and_invalid_characters():
    for edit in (lambda v: v.update(description="ą" * 2600),
                 lambda v: v.update(keywords=[str(i) + "x" * 79 for i in range(7)]),
                 lambda v: v["titles"].update(search="A <bad> title")):
        value = metadata(); edit(value)
        with pytest.raises(ValueError): validate_metadata(value)
