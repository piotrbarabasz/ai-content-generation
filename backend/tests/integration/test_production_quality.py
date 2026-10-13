"""Real CPU media inspection and retained publishing/caption adapters, all offline."""
import asyncio
from dataclasses import replace
from hashlib import sha256
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from app.application.production_quality import ProductionQualityService
from app.application.quality_sources import ProjectQualitySources, parse_srt
from app.domain.production_quality import QAContext, QARunConfiguration
from app.providers.ffmpeg_quality import FFmpegQAInspector
from app.runtime.media_process import MediaProcess
from app.storage.production_quality import ProjectQuality
from app.storage.local_store import LocalArtifactStore
from tests.integration.test_publishing_package import publishing, approved, add_alignment, setup, prepared


@pytest.fixture
def media(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe: pytest.skip("Local FFmpeg/FFprobe unavailable")
    path=tmp_path/"synthetic.mp4"
    subprocess.run([ffmpeg,"-nostdin","-v","error","-n","-f","lavfi","-i","color=c=black:s=320x180:r=25:d=1",
        "-f","lavfi","-i","testsrc2=s=320x180:r=25:d=2","-f","lavfi","-i","aevalsrc=0:s=48000:d=1",
        "-f","lavfi","-i","sine=frequency=440:sample_rate=48000:duration=2",
        "-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a]concat=n=2:v=0:a=1[a]",
        "-map","[v]","-map","[a]","-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",
        "-c:a","aac","-ar","48000","-threads","1",str(path)],check=True,timeout=30)
    return NS(path=path,inspector=FFmpegQAInspector(ffmpeg,ffprobe),root=tmp_path)


def test_real_cpu_probe_full_decode_audio_black_freeze_samples_and_immutable_source(media):
    q=media; before=q.path.read_bytes(); cfg=QARunConfiguration(mode="deep",silence_seconds=.5,freeze_seconds=.5,max_frames=2)
    async def scan():
        kw={"canceled":lambda:False,"progress":lambda *_:None}
        p=await q.inspector.probe(q.path,q.root,cfg,**kw)
        d=await q.inspector.decode(q.path,q.root,cfg,**kw)
        a=await q.inspector.audio(q.path,q.root,cfg,**kw)
        v=await q.inspector.visual(q.path,q.root,cfg,**kw)
        frames=await q.inspector.frames(q.path,q.root,[{"start":0,"end":1},{"start":1,"end":3}],cfg,**kw)
        return p,d,a,v,frames
    p,d,a,v,frames=asyncio.run(scan())
    assert p["frame_count_method"]=="container_metadata" and p["video_streams"][0]["frame_count"]==75
    assert d["decoded_frames"]==75 and d["fully_decoded"]
    assert a["integrated_lufs"] is not None and a["true_peak_dbtp"] is not None and a["silence"][0][1]>=.99
    assert v["black"] and v["freeze"] and len(frames)==2 and all(f["raw"].startswith(b"\x89PNG") for f in frames)
    assert q.path.read_bytes()==before


def test_real_report_evidence_bounded_export_and_reopen(media):
    q=media; store=LocalArtifactStore(q.root/"artifacts")
    m=store.save_artifact("video.mp4",q.path.read_bytes(),{"artifact_type":"video_render","project_id":"project"})
    c=QAContext("project",m.artifact_id,m.checksum,m.size_bytes,"original",store._artifact_path(m.storage_key),
        {"owner_ok":True,"mapping_available":True,"duration_profile":"social","durations":{"actual_narration_seconds":3},
         "expected":{"width":320,"height":180,"fps":25,"frame_count":75},"captions":{"state":"not_requested"},
         "shots":[{"id":"black","start":0,"end":1,"motion":"unknown","mode":"fill"},{"id":"pattern","start":1,"end":3,"motion":"unknown","mode":"fill"}]})
    s=ProductionQualityService(NS(workspace=q.root,capture=lambda _:c),ProjectQuality(store,"project"),q.inspector)
    report=asyncio.run(s.run(m.artifact_id,QARunConfiguration(mode="deep",freeze_seconds=.5,max_frames=2,max_evidence=1)))
    _,r=s.records.report(report.artifact_id)
    assert r.state()=="BLOCKED" and r.measurements["fully_decoded"]
    assert len(s.records.history("evidence"))==1
    s.records.export(report.artifact_id)
    assert s.records.history("export_json") and s.records.history("export_markdown")
    assert ProductionQualityService(s.sources,s.records,q.inspector).current(m.artifact_id,m.checksum)["state"]=="BLOCKED"


def test_quick_command_does_not_decode_count_frames(media):
    commands=[]
    class Process:
        async def run(self,args,**kwargs): commands.append(args); return await MediaProcess().run(args,**kwargs)
    inspector=FFmpegQAInspector(media.inspector.ffmpeg,media.inspector.ffprobe,process_factory=lambda **_:Process())
    asyncio.run(inspector.probe(media.path,media.root,QARunConfiguration(),canceled=lambda:False,progress=lambda *_:None))
    assert len(commands)==1 and "-count_frames" not in commands[0] and "-vf" not in commands[0]


def test_streaming_stderr_retains_all_lines_beyond_tail_and_partial_eof(tmp_path):
    lines=[]
    command=[sys.executable,"-c","import sys; [print('x'*100,file=sys.stderr) for i in range(1000)]; sys.stderr.write('EOF')"]
    tail=asyncio.run(MediaProcess().run(command,cwd=tmp_path,capture_stderr=True,on_stderr_line=lines.append))
    assert len(lines)==1001 and lines[-1]=="EOF" and len(tail)<=65536


def adapter(f):
    f.service.renders.index=NS(selected=lambda:{"project:video_render":f.selected[0].artifact_id})
    return ProjectQualitySources(f.service)


def test_adapter_no_auto_caption_generation_and_current_mapping(publishing):
    f=publishing;sources=adapter(f); before=[m.artifact_id for m in f.store.list_artifacts()]
    c=sources.capture(f.source.artifact_id)
    assert c.facts["mapping_available"] and c.facts["captions"]["state"]=="not_requested"
    assert [m.artifact_id for m in f.store.list_artifacts()]==before
    f.current[0]=None
    assert sources.capture(f.source.artifact_id).facts["stale"]


def test_adapter_verified_word_captions_and_corrupt_srt_block(publishing):
    f=publishing;sources=adapter(f); add_alignment(f)
    subtitle=f.service.subtitles("original")
    c=sources.capture(f.source.artifact_id)
    assert c.facts["captions"]["state"]=="valid" and c.facts["verified_speech"]
    f.store._artifact_path(subtitle.storage_key).write_bytes(b"corrupt UTF8 SRT")
    assert sources.capture(f.source.artifact_id).facts["captions"]["state"]=="stale"


def test_publish_qa_metadata_blocks_false_approval_and_updates_package_identity(publishing):
    f=publishing;s=f.service;revision=approved(f)
    value={"state":"BLOCKED","approved":False,"report_id":"qa-1","report_checksum":"a"*64,"review_acknowledged":True}
    s.quality=NS(publishing=lambda *_:dict(value))
    first=s.build_package(revision)
    import json,zipfile
    with zipfile.ZipFile(s.store._artifact_path(first.storage_key)) as archive:
        meta=json.loads(archive.read("publishing-package/metadata.json"))
    assert meta["qa"]["state"]=="BLOCKED" and not meta["qa"]["approved"]
    value.update(state="STALE",report_id="qa-2")
    second=s.build_package(revision)
    assert second.artifact_id!=first.artifact_id and second.metadata["identity"]["qa"]["state"]=="STALE"


@pytest.mark.parametrize("raw",[b"\xff",b"1\n00:00:00,000 --> 00:00:00,000\nx",b"1\n00:70:00,000 --> 00:70:01,000\nx"])
def test_srt_utf8_and_timing_reject_invalid(raw):
    with pytest.raises((ValueError,UnicodeError)):parse_srt(raw)


def test_branded_caption_shift_and_delivery_targets_actual_contract(publishing):
    from dataclasses import asdict
    from fractions import Fraction
    from app.domain.branding import BrandingConfig
    from app.domain.delivery import DeliveryConfig,DELIVERY_PRESETS
    f=publishing;sources=adapter(f);add_alignment(f);original_subtitles=f.service.subtitles("original")
    branding=BrandingConfig(intro=True,outro=True)
    f.service.finishing=NS(branding=NS(current=lambda:branding))
    raw=b"verified branded fixture";duration=f.timeline.video_duration+18
    e={**f.source.metadata["render"],"checksum":sha256(raw).hexdigest(),"size_bytes":len(raw),
       "frame_count":f.timeline.total_frames+450,"video_duration":[duration.numerator,duration.denominator],
       "audio_duration":[(f.timeline.duration+18).numerator,(f.timeline.duration+18).denominator]}
    branded=f.store.save_artifact("branded.mp4",raw,{"project_id":f.session.project.id,"artifact_type":"branded_video","finishing":e,
        "identity":{"source_id":f.source.artifact_id,"source_checksum":f.source.checksum,"settings":branding.to_payload()}})
    from app.domain.caption_track import PublishedCaptionTrack,serialize_srt
    from app.domain.publishing_package import CompositionTiming
    payload=original_subtitles.metadata["identity"]["track"]
    shifted=CompositionTiming(f.timeline.duration,duration,intro=3,outro=15).captions(PublishedCaptionTrack.from_payload(payload).track.segments)
    f.store.save_artifact("branded.srt",serialize_srt(shifted),{"project_id":f.session.project.id,"artifact_type":"publishing_subtitles",
        "identity":{"source_id":branded.artifact_id,"source_checksum":branded.checksum,"track":payload}})
    c=sources.capture(branded.artifact_id)
    assert c.facts["mapping_available"] and len(c.facts["branding_intervals"])==2
    assert c.facts["captions"]["state"]=="valid" and c.facts["captions"]["segments"][0]["start_ms"]>=3000
    assert c.facts["durations"]["original_video_seconds"]+18==c.facts["durations"]["branded_video_seconds"]
    config=DeliveryConfig(source="branded",target_lufs=-20,target_peak=-3)
    f.service.delivery=NS(artifacts=NS(current=lambda:config))
    raw=b"verified delivery fixture";e={**e,"checksum":sha256(raw).hexdigest(),"size_bytes":len(raw),"source_artifact_id":branded.artifact_id,"source_checksum":branded.checksum}
    delivery=f.store.save_artifact("delivery.mp4",raw,{"project_id":f.session.project.id,"artifact_type":"delivery_video","delivery":e,
        "identity":{"source_artifact_id":branded.artifact_id,"source_checksum":branded.checksum,"settings":config.to_payload(),"preset":asdict(DELIVERY_PRESETS[config.preset])}})
    c=sources.capture(delivery.artifact_id)
    assert c.facts["mapping_available"] and c.facts["delivery_targets"]=={"target_lufs":-20,"target_peak":-3}
    assert c.facts["durations"]["delivery_video_seconds"]==float(duration)


def test_quality_panel_open_is_metadata_only_and_controls_history_cancel(qa_panel):
    p=qa_panel
    assert not p.service.inspector.calls and p.quick_button.isEnabled() and p.deep_button.isEnabled()
    p.start(p.scan("quick"))
    import time
    deadline=time.monotonic()+10
    while p.busy and time.monotonic()<deadline:p.tick();time.sleep(.005)
    assert not p.busy and p.history.count()==1 and p.status.text()=="READY"
    p.thresholds["true_peak"].setValue(-2)
    assert p.status.text()=="STALE"
    p.start(p.scan("deep"));p.cancel()
    while p.busy and time.monotonic()<deadline:p.tick();time.sleep(.005)
    assert not p.busy and p.service.records.history()


@pytest.fixture
def qa_panel(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    from PySide6.QtWidgets import QApplication
    from app.desktop.quality_panel import QualityPanel
    from tests.unit.test_production_quality import qa
    f=qa.__wrapped__(tmp_path)
    f.service.sources.publishing=NS(renders=NS(index=NS(selected=lambda:{"project:video_render":f.source.artifact_id})))
    app=QApplication.instance() or QApplication([])
    p=QualityPanel();p.bind(f.service);p.activate()
    yield p
    p.player.stop();p.close()
