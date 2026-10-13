"""Offline production QA contracts and deterministic classification, never generation."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace as NS

import pytest

from app.application.production_quality import ProductionQualityService
from app.application.quality_validators import (SourceValidator, TechnicalValidator, DurationValidator,
    CaptionValidator, AudioValidator, VisualValidator, PacingValidator, finding)
from app.domain.production_quality import QAContext, QAReport, QARunConfiguration
from app.runtime.media_process import RenderCanceled
from app.storage.local_store import LocalArtifactStore
from app.storage.production_quality import ProjectQuality


def probe(duration=600, width=1920, height=1080):
    return {"container": "mov,mp4,m4a,3gp,3g2,mj2", "video_streams": [{"codec": "h264", "profile": "High",
        "pixel_format": "yuv420p", "width": width, "height": height, "fps": 25, "frame_count": round(duration*25),
        "duration": duration, "field_order": "progressive", "bitrate_bps": 100000}],
        "audio_streams": [{"codec": "aac", "profile": "LC", "sample_rate": 48000, "channels": 1, "duration": duration}]}


@pytest.fixture
def qa(tmp_path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    source = store.save_artifact("video.mp4", b"immutable production fixture", {"artifact_type": "video_render", "project_id": "project"})
    path = store._artifact_path(source.storage_key)
    facts = {"owner_ok": True, "stale": False, "mapping_available": True, "duration_profile": "standard",
        "durations": {"planned_narration_seconds": 600, "actual_narration_seconds": 600, "original_video_seconds": 600},
        "expected": {"width": 1920, "height": 1080, "fps": 25, "frame_count": 15000, "video_duration": 600, "audio_duration": 600},
        "captions": {"state": "not_requested"}, "shots": [{"id": "one", "start": 0, "end": 600,
            "image_id": "image", "image_checksum": "a"*64, "mode": "fit", "motion": "static"}]}
    context = QAContext("project", source.artifact_id, source.checksum, source.size_bytes, "original", path, facts)
    sources = NS(workspace=tmp_path, capture=lambda _: context, candidates=lambda: [source])
    class Inspector:
        def __init__(self): self.calls = []; self.version = "one"; self.fail = None; self.on_probe = lambda: None
        def identity(self): return {"adapter": self.version}
        async def probe(self, *_args, **_kwargs):
            self.calls.append("probe"); self.on_probe()
            if self.fail: raise self.fail
            return probe()
        async def decode(self, *_args, **_kwargs): self.calls.append("decode"); return {"decoded_frames": 15000, "fully_decoded": True}
        async def audio(self, *_args, **_kwargs): self.calls.append("audio"); return {"integrated_lufs": -16, "true_peak_dbtp": -2, "silence": []}
        async def visual(self, *_args, **_kwargs): self.calls.append("visual"); return {"black": [], "freeze": []}
        async def frames(self, *_args, **_kwargs): self.calls.append("frames"); return []
    inspector = Inspector(); records = ProjectQuality(store, "project")
    service = ProductionQualityService(sources, records, inspector)
    return NS(service=service, records=records, context=context, inspector=inspector, store=store, source=source)


def rules(validator, context, measurements, config=QARunConfiguration()):
    return {f.rule_id: f for f in validator().validate(context, config, measurements)}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, 121])
def test_sampling_configuration_strict_and_bounded(value):
    with pytest.raises(ValueError): QARunConfiguration(max_frames=value)


@pytest.mark.parametrize("profile,duration,blocked", [("standard",540,False),("standard",660,False),("standard",489.44,True),
    ("social",30,False),("social",60,False),("short",29.99,True),("social",61,True)])
def test_format_narration_range_excludes_branded_padding(qa, profile, duration, blocked):
    c = replace(qa.context, facts={**qa.context.facts, "duration_profile": profile,
        "durations": {"actual_narration_seconds": duration, "branded_video_seconds": duration+22}})
    result = rules(DurationValidator, c, {"probe": probe(duration+22)})
    assert ("DURATION_OUTSIDE_PROFILE" in result) == blocked


def test_unknown_mapping_does_not_invent_duration(qa):
    c = replace(qa.context, facts={"duration_profile": "standard"})
    assert "DURATION_ACCEPTANCE_UNAVAILABLE" in rules(DurationValidator, c, {"probe": probe()})


@pytest.mark.parametrize("field,value,rule", [("codec","hevc","VIDEO_CODEC_PROFILE"),("pixel_format","yuv444p","VIDEO_CODEC_PROFILE"),
    ("width",1080,"OUTPUT_WIDTH"),("height",1920,"OUTPUT_HEIGHT"),("fps",30,"OUTPUT_FPS"),
    ("frame_count",100,"FRAME_COUNT_INVALID"),("duration",599,"VIDEO_TRUNCATED_OR_CHANGED"),("field_order","tt","INTERLACED_VIDEO")])
def test_technical_mismatch_blocks(qa, field, value, rule):
    p = probe(); p["video_streams"][0][field] = value
    assert rules(TechnicalValidator, qa.context, {"probe": p})[rule].severity == "BLOCKED"


def test_stream_count_and_av_mismatch_block_but_low_bitrate_does_not(qa):
    p = probe(); assert not any(f.severity == "BLOCKED" for f in rules(TechnicalValidator, qa.context, {"probe": p}).values())
    p["audio_streams"][0]["duration"] = 590
    assert "AV_DESYNCHRONIZATION" in rules(TechnicalValidator, qa.context, {"probe": p})
    p["audio_streams"] = []
    assert "AUDIO_MISSING_OR_EXTRA" in rules(TechnicalValidator, qa.context, {"probe": p})
    p["video_streams"] = []
    assert "VIDEO_STREAM_COUNT" in rules(TechnicalValidator, qa.context, {"probe": p})


@pytest.mark.parametrize("segments", [[{"start_ms":0,"end_ms":0,"text":"x"}],
    [{"start_ms":0,"end_ms":200,"text":"x"},{"start_ms":100,"end_ms":300,"text":"y"}]])
def test_caption_invalid_timing_blocks(qa, segments):
    c = replace(qa.context, facts={**qa.context.facts, "captions":{"state":"valid","segments":segments}})
    assert "CAPTION_TIMING_INVALID" in rules(CaptionValidator,c,{"probe":probe()})


def test_caption_coverage_needs_independent_speech_and_handles_utf8(qa):
    c = replace(qa.context, facts={**qa.context.facts, "captions":{"state":"valid", "segments":[{"start_ms":1000,"end_ms":2000,"text":"Za\u017c\u00f3\u0142\u0107"}]}})
    m = {"probe":probe()}; rules(CaptionValidator,c,m,QARunConfiguration(mode="deep"))
    assert m["caption_coverage"]["state"] == "UNAVAILABLE"
    c = replace(c, facts={**c.facts,"verified_speech":[[1,3]]})
    result = rules(CaptionValidator,c,m,QARunConfiguration(mode="deep"))
    assert m["caption_coverage"]["ratio"] == .5 and result["CAPTION_COVERAGE_LOW"].severity == "REVIEW"


def test_intentional_cards_static_fit_and_editorial_targets_are_advisory(qa):
    c = replace(qa.context, facts={**qa.context.facts,"intentional_silence":[[0,5]],"branding_intervals":[[0,5]],
        "delivery_targets":{"target_lufs":-20,"target_peak":-3}})
    m = {"audio":{"integrated_lufs":-20,"true_peak_dbtp":-4,"silence":[[0,5],[10,15]]},
         "visual":{"black":[[0,5]],"freeze":[[0,600]]}}
    result = rules(AudioValidator,c,m)
    assert "AUDIO_INTEGRATED_LUFS" not in result and result["INTENTIONAL_SILENCE"].severity == "INFO"
    assert result["SUSPICIOUS_NARRATION_SILENCE"].severity == "REVIEW"
    assert all(f.severity == "INFO" for f in rules(VisualValidator,c,m).values())
    assert rules(AudioValidator,c,m,QARunConfiguration(inherit_delivery=False))["AUDIO_INTEGRATED_LUFS"].severity == "REVIEW"


def test_repetition_shot_boundaries_crop_and_pacing_remain_review(qa):
    shots = [dict(qa.context.facts["shots"][0],id=str(i),start=i*30,end=(i+1)*30,mode="fill",crop_fraction=.3) for i in range(6)]
    c=replace(qa.context,facts={**qa.context.facts,"shots":shots})
    result=VisualValidator().validate(c,QARunConfiguration(),{"visual":{}})
    assert sum(f.rule_id=="EXACT_REUSED_IMAGE" for f in result)==5
    assert all(f.severity=="REVIEW" for f in result)
    assert "REPEATED_PACING_PATTERN" in rules(PacingValidator,c,{})
    c=replace(c,facts={**c.facts,"shots":[dict(shots[0],id="same"),dict(shots[1],id="same")]})
    assert "EXACT_REUSED_IMAGE" not in rules(VisualValidator,c,{"visual":{}})


def test_quick_read_only_cache_and_reopen(qa):
    before=qa.context.path.read_bytes()
    first=asyncio.run(qa.service.run(qa.source.artifact_id))
    _,r=qa.records.report(first.artifact_id)
    assert r.state()=="READY" and qa.inspector.calls==["probe"]
    assert asyncio.run(qa.service.run(qa.source.artifact_id))==first
    reopened=ProductionQualityService(qa.service.sources,qa.records,qa.inspector)
    assert reopened.current(qa.source.artifact_id,qa.source.checksum)["state"]=="READY"
    assert qa.context.path.read_bytes()==before
    assert all(m.artifact_type.startswith("quality_") or m==qa.source for m in qa.store.list_artifacts())


def test_deep_is_explicit_and_semantic_unavailable_never_claims_ready(qa):
    m=asyncio.run(qa.service.run(qa.source.artifact_id,QARunConfiguration(mode="deep",semantic=True,evidence=False)))
    _,r=qa.records.report(m.artifact_id)
    assert qa.inspector.calls==["probe","decode","audio","visual","frames"]
    assert next(v for v in r.outcomes if v["validator_id"]=="semantic")["state"]=="UNAVAILABLE"
    assert r.state()=="REVIEW_REQUIRED" and r.measurements["decoded_frames"]==15000


def test_checksum_failure_never_inspects_or_repairs_source(qa):
    qa.context.path.write_bytes(b"changed bytes")
    m=asyncio.run(qa.service.run(qa.source.artifact_id))
    _,r=qa.records.report(m.artifact_id)
    assert r.state()=="BLOCKED" and not qa.inspector.calls
    assert qa.context.path.read_bytes()==b"changed bytes"
    with pytest.raises(ValueError,match="integrity"):
        qa.records.review(m.artifact_id,r.findings[0].id,"intentional","ignore")


def test_missing_source_foreign_owner_and_mapping_errors(qa):
    c=replace(qa.context,facts={"owner_ok":False,"integrity_errors":["ownership mismatch"]})
    result=rules(SourceValidator,c,{"source_missing":True})
    assert set(result)=={"SOURCE_MISSING","SOURCE_OWNERSHIP_INVALID","SOURCE_MAPPING_INVALID"}


def test_replacement_thresholds_mapping_and_tool_version_stale_previous_report(qa):
    m=asyncio.run(qa.service.run(qa.source.artifact_id))
    qa.service.config=replace(qa.service.config,true_peak=-2)
    assert qa.service.view(m.artifact_id)[2]=="STALE"
    qa.service.config=QARunConfiguration();qa.inspector.version="two"
    assert qa.service.view(m.artifact_id)[2]=="STALE"
    qa.inspector.version="one";qa.context.path.write_bytes(b"source replacement")
    assert qa.service.view(m.artifact_id)[2]=="STALE"


def test_corrupt_decode_provider_error_and_cancel_preserve_reports(qa):
    first=asyncio.run(qa.service.run(qa.source.artifact_id))
    qa.inspector.fail=RuntimeError("corrupt container")
    m=asyncio.run(qa.service.run(qa.source.artifact_id,reuse=False))
    assert qa.records.report(m.artifact_id)[1].state()=="BLOCKED"
    with pytest.raises(RenderCanceled): asyncio.run(qa.service.run(qa.source.artifact_id,reuse=False,canceled=lambda:True))
    assert not qa.service.running and qa.records.report(first.artifact_id)[1].state()=="READY"
    assert qa.records.report(qa.records.history()[-1].artifact_id)[1].state()=="CANCELED"


def test_source_changed_during_scan_blocks_without_repair(qa):
    qa.inspector.on_probe=lambda:qa.context.path.write_bytes(b"replaced during probe")
    m=asyncio.run(qa.service.run(qa.source.artifact_id))
    assert "SOURCE_CHANGED_DURING_QA" in {f.rule_id for f in qa.records.report(m.artifact_id)[1].findings}


def test_reviews_raw_immutable_export_and_publishing_ack_bound_exact_report(qa):
    m=asyncio.run(qa.service.run(qa.source.artifact_id,QARunConfiguration(mode="deep",evidence=False)))
    raw=qa.records.bytes(m);_,r=qa.records.report(m.artifact_id)
    f=next(f for f in r.findings if f.severity=="REVIEW")
    with pytest.raises(ValueError,match="acknowledge"):qa.service.publishing(qa.source.artifact_id,qa.source.checksum)
    qa.service.acknowledge_publishing(qa.source.artifact_id,qa.source.checksum,"Reviewed pacing")
    assert qa.service.publishing(qa.source.artifact_id,qa.source.checksum)["review_acknowledged"]
    with pytest.raises(ValueError,match="new completed"):qa.records.review(m.artifact_id,f.id,"resolved","fixed")
    qa.records.review(m.artifact_id,f.id,"intentional","Static documentary pacing is intentional")
    assert qa.records.bytes(m)==raw and qa.service.view(m.artifact_id)[2]=="READY"
    a,b=qa.records.export(m.artifact_id)
    assert qa.records.read(a)["manual_decisions"] and b"Production QA report" in qa.records.bytes(b)
    qa.context.path.write_bytes(b"changed")
    assert not qa.service.current(qa.source.artifact_id,qa.source.checksum)["approved"]


def test_report_roundtrip_and_finding_tampering_rejected(qa):
    m=asyncio.run(qa.service.run(qa.source.artifact_id));r=qa.records.report(m.artifact_id)[1]
    assert QAReport.from_payload(r.to_payload())==r
    payload=deepcopy(r.to_payload());payload["findings"][0]["actual"]="tampered"
    with pytest.raises(ValueError,match="identity"):QAReport.from_payload(payload)
    with pytest.raises(ValueError,match="Semantic"):
        finding(qa.context,"SEMANTIC","BLOCKED","semantic","opinion","review")


def test_caption_past_video_and_readability(qa):
    c=replace(qa.context,facts={**qa.context.facts,"captions":{"state":"valid","segments":[{"start_ms":599000,"end_ms":601000,"text":"x"*80}]}})
    r=rules(CaptionValidator,c,{"probe":probe()})
    assert r["CAPTION_PAST_VIDEO"].severity=="BLOCKED" and r["CAPTION_READABILITY"].severity=="REVIEW"


def test_excess_peak_and_near_duplicates_advisory(qa):
    m={"audio":{"integrated_lufs":-16,"true_peak_dbtp":.5,"silence":[]},"visual":{},
       "near_duplicates":[{"start":0,"end":20,"distance":1,"references":["one","two"]}]}
    assert rules(AudioValidator,qa.context,m)["AUDIO_TRUE_PEAK_DBTP"].severity=="REVIEW"
    assert rules(VisualValidator,qa.context,m)["POSSIBLE_REPEATED_VISUAL"].severity=="REVIEW"


def test_optional_semantic_only_review_and_no_call_without_explicit_request(qa):
    class Semantic:
        id,version="semantic","1"
        def __init__(self):self.calls=0
        def identity(self):return {"model":"offline-fixture"}
        def validate(self,c,*_):self.calls+=1;return (finding(c,"WEAK_CONCEPT","REVIEW","semantic","Mock editorial opinion","Inspect scene"),)
    v=Semantic();qa.service.semantic_validator=v
    asyncio.run(qa.service.run(qa.source.artifact_id));assert v.calls==0
    m=asyncio.run(qa.service.run(qa.source.artifact_id,QARunConfiguration(semantic=True)))
    assert v.calls==1 and qa.records.report(m.artifact_id)[1].state()=="REVIEW_REQUIRED"


def test_validator_filter_failure_unavailable_and_source_decode_failure_blocked(qa):
    async def failure(*_,**__):raise RuntimeError("filter unavailable")
    qa.inspector.audio=failure
    m=asyncio.run(qa.service.run(qa.source.artifact_id,QARunConfiguration(mode="deep",evidence=False)))
    assert next(v for v in qa.records.report(m.artifact_id)[1].outcomes if v["validator_id"]=="audio")["state"]=="UNAVAILABLE"
    qa.inspector.decode=failure
    m=asyncio.run(qa.service.run(qa.source.artifact_id,QARunConfiguration(mode="deep",evidence=False),reuse=False))
    assert qa.records.report(m.artifact_id)[1].state()=="BLOCKED"


def test_review_resolution_requires_new_matching_source_and_executed_check(qa):
    old=asyncio.run(qa.service.run(qa.source.artifact_id,QARunConfiguration(mode="deep",evidence=False)))
    r=qa.records.report(old.artifact_id)[1];f=next(f for f in r.findings if f.rule_id=="SHOT_LENGTH_REVIEW")
    qa.context.facts["shots"][0]["end"]=10
    newer=asyncio.run(qa.service.run(qa.source.artifact_id,QARunConfiguration(mode="deep",evidence=False),reuse=False))
    qa.records.review(old.artifact_id,f.id,"resolved","New pacing check passed",new_report_id=newer.artifact_id)
    assert qa.records.decisions(r)[0]["status"]=="resolved"


def test_requested_missing_mapping_cannot_be_ready(qa):
    qa.context.facts.update(mapping_available=False,mapping_reason="No retained Timeline")
    m=asyncio.run(qa.service.run(qa.source.artifact_id))
    assert qa.records.report(m.artifact_id)[1].state()=="REVIEW_REQUIRED"
