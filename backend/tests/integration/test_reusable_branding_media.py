"""Real short CPU FFmpeg fixtures; no AI, GPU or model provisioning."""
import asyncio
from array import array
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
import shutil
import subprocess
from types import SimpleNamespace as NS

from PIL import Image
import pytest

from app.application.channel_library import ChannelLibraryService
from app.application.video_finishing import VideoFinishingService
from app.application.projects import ProjectSession
from app.domain.branding_timing import verified_timing_map
from app.domain.publishing_package import CompositionTiming
from app.domain.caption_track import CaptionSegment
from app.providers.ffmpeg_finishing import FFmpegFinisher
from app.runtime.media_process import RenderCanceled
from app.storage.channel_library import ChannelLibrary
from app.storage.branding import ProjectBranding
from app.storage.local_store import LocalArtifactStore
from app.storage.project_repository import ProjectRepository
from tests.integration.test_video_finishing_media import source


def clip(renderer,path,*,seconds=1,fps=30,color="green",audio=True,dimensions="320x180",frequency=880):
    seconds=float(seconds)
    command=[str(renderer.ffmpeg),"-nostdin","-v","error","-f","lavfi","-i",f"color=c={color}:s={dimensions}:r={fps}:d={seconds}"]
    if audio:command += ["-f","lavfi","-i",f"sine=frequency={frequency}:sample_rate=44100:duration={seconds}"]
    command += ["-c:v","libx264","-preset","ultrafast","-threads","2","-pix_fmt","yuv420p"]
    if audio:command += ["-c:a","aac","-ar","44100","-ac","2"]
    command += ["-movflags","+faststart",str(path)]
    subprocess.run(command,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    return path


@pytest.fixture
def shared(source,tmp_path):
    root,renderer,evidence=source
    library=ChannelLibrary(tmp_path/"shared")
    profile=library.create("Documentary Test Channel")
    service=ChannelLibraryService(library,renderer)
    logo=asyncio.run(service.import_asset(profile.channel_id,"logo",root/"logo.png"))
    intro=asyncio.run(service.import_asset(profile.channel_id,"intro",clip(renderer,root/"intro-import.mp4",seconds=Fraction(3,2))))
    outro=asyncio.run(service.import_asset(profile.channel_id,"outro",clip(renderer,root/"outro-import.mp4",seconds=1,audio=False,color="yellow")))
    profile=library.edit(profile,logo_version=logo["version_id"],intro_version=intro["version_id"],outro_version=outro["version_id"])
    return NS(root=root,renderer=renderer,evidence=evidence,library=library,profile=profile,creator=service,intro=intro,outro=outro)


def frequency_at(renderer,path,second):
    raw=subprocess.check_output([str(renderer.ffmpeg),"-v","error","-ss",str(second),"-i",str(path),
        "-t","0.5","-map","0:a:0","-f","s16le","-ac","1","-ar","48000","-"])
    samples=array("h");samples.frombytes(raw)
    crossings=0;negative=False
    for sample in samples:
        if sample < -300:negative=True
        elif sample > 300 and negative:crossings+=1;negative=False
    return crossings*2,max(map(abs,samples))


@pytest.mark.parametrize("after",[False,True])
def test_audio_fps_source_preservation_and_piecewise_timing(shared,after):
    f=shared
    config=f.library.selection(f.profile,placement="after_cold_open" if after else "before")
    assets=f.library.resolve_selection(config)
    clips={k:{**assets[k],"path":f.library.path(assets[k]["version_id"])} for k in ("intro","outro")}
    before=sha256((f.root/"source.mp4").read_bytes()).hexdigest()
    boundary={"video_frame":50,"audio_sample":96000,"timeline_id":"timeline"} if after else None
    report=asyncio.run(f.renderer.finish(f.root,f.evidence,config,boundary,clips=clips,canceled=lambda:False,progress=lambda *_:None))
    mapped=report["timing_map"]
    assert report["video_codec"]=="h264" and report["audio_codec"]=="aac" and report["sample_rate"]==48000
    assert (report["width"],report["height"],report["fps"])==(640,360,"25")
    assert mapped["intro"]["frames"]==38 and mapped["intro"]["samples"]==72960
    assert report["frame_count"]==100+38+25
    assert Fraction(*report["video_duration"])==Fraction(163,25)
    assert sha256((f.root/"source.mp4").read_bytes()).hexdigest()==before
    # The 30 FPS/44.1 kHz stereo intro is preserved and normalized to the mono source.
    intro_start=2 if after else 0
    assert abs(frequency_at(f.renderer,f.root/"branded.mp4",intro_start+.5)[0]-880)<8
    assert abs(frequency_at(f.renderer,f.root/"branded.mp4",1+(0 if after else 1.52))[0]-220)<8
    assert abs(frequency_at(f.renderer,f.root/"branded.mp4",3+1.52)[0]-440)<8
    assert frequency_at(f.renderer,f.root/"branded.mp4",6)[1]<60
    timing=CompositionTiming(4,Fraction(163,25),intro=Fraction(38,25),insertion=2 if after else 0,outro=1)
    captions=timing.captions((CaptionSegment("a",1,0,1000,"Hook"),CaptionSegment("b",2,2000,3000,"Introduction")))
    assert captions[0].start_ms==(0 if after else 1520)
    assert captions[1].start_ms==3520
    assert timing.point(3)==Fraction(113,25)
    manifest=NS(checksum=report["checksum"],metadata={"finishing":report,"identity":{"source_id":"source","source_checksum":before,"boundary":boundary,"settings":config.to_payload()}})
    original=NS(artifact_id="source",checksum=before,metadata={"render":{**f.evidence,"sample_rate":48000}})
    assert verified_timing_map(manifest,original,NS(id="timeline"))==mapped
    report["timing_map"]["source_intervals"][-1]["output_frames"][0]+=1
    with pytest.raises(ValueError,match="intervals"):verified_timing_map(manifest,original,NS(id="timeline"))


def test_real_corrupt_clip_is_rejected_and_imported_bytes_survive_source_removal(shared):
    f=shared
    path=f.root/"bad.mp4";path.write_bytes(b"not an MP4")
    with pytest.raises((ValueError,RuntimeError)):
        asyncio.run(f.creator.import_asset(f.profile.channel_id,"intro",path))
    (f.root/"intro-import.mp4").unlink()
    assert f.library.path(f.intro["version_id"]).exists()
    assert f.library.asset(f.outro["version_id"])["media"]["has_audio"] is False


def test_aspect_ratio_reject_or_contain_without_stretch(shared):
    f=shared
    path=clip(f.renderer,f.root/"portrait.mp4",dimensions="180x320",audio=False)
    asset=asyncio.run(f.creator.import_asset(f.profile.channel_id,"intro",path))
    ref={"version_id":asset["version_id"],"checksum":asset["checksum"],"duration":asset["media"]["video_duration"]}
    config=f.library.selection(f.profile,intro_asset=ref,outro=False,placement="before",fit="reject")
    clips={"intro":{**asset,"path":f.library.path(asset["version_id"])}}
    with pytest.raises(ValueError,match="aspect ratio"):
        asyncio.run(f.renderer.finish(f.root,f.evidence,config,None,clips=clips,canceled=lambda:False,progress=lambda *_:None))
    report=asyncio.run(f.renderer.finish(f.root,f.evidence,replace(config,fit="contain"),None,clips=clips,canceled=lambda:False,progress=lambda *_:None))
    assert report["frame_count"]==125
    # Left edge of portrait insertion is black padding, not a stretched image.
    raw=subprocess.check_output([str(f.renderer.ffmpeg),"-v","error","-ss","0.5","-i",str(f.root/"branded.mp4"),
        "-frames:v","1","-vf","crop=2:2:0:0","-f","rawvideo","-pix_fmt","rgb24","-"])
    assert max(raw)<10


def test_one_time_logo_clip_creation_and_immutable_version_reuse(shared):
    f=shared
    first=asyncio.run(f.creator.create_clip(f.profile,"intro",width=640,height=360))
    second=asyncio.run(f.creator.create_clip(f.profile,"intro",width=640,height=360))
    assert first==second and first["media"]["video_duration"]==[3,1]
    outro=asyncio.run(f.creator.create_clip(f.profile,"outro",width=640,height=360))
    assert outro["media"]["video_duration"]==[15,1]
    old=f.library.path(first["version_id"]).read_bytes()
    updated=f.library.edit(f.profile,tagline="New version")
    newer=asyncio.run(f.creator.create_clip(updated,"intro",width=640,height=360))
    assert newer["version_id"]!=first["version_id"] and newer["asset_id"]==first["asset_id"]
    assert f.library.path(first["version_id"]).read_bytes()==old


def test_two_projects_reuse_shared_clips_cache_and_rebuild_only_finishing(shared,tmp_path):
    f=shared;config=f.library.selection(f.profile,placement="before")
    outputs=[];sessions=[]
    try:
        for name in ("Project A","Project B"):
            session=ProjectSession.create(tmp_path/name,repository_factory=ProjectRepository,name=name);sessions.append(session)
            store=LocalArtifactStore.for_project(session.repository)
            raw=(f.root/"source.mp4").read_bytes()
            original=store.save_artifact("original.mp4",raw,{"artifact_type":"video_render","project_id":session.project.id,"render":f.evidence})
            renders=NS(selected=lambda **_:original)
            service=VideoFinishingService(session,ProjectBranding(store,session.project.id),renders,f.renderer,channel_library=f.library)
            result=asyncio.run(service.build(config))
            assert asyncio.run(service.build(config))==result
            assert store.read_artifact(original.storage_key)==raw
            assert session.active_script.sections==()
            outputs.append((service,result,original))
        changed=asyncio.run(f.creator.import_asset(f.profile.channel_id,"intro",clip(f.renderer,f.root/"v2.mp4",color="cyan")))
        newer=f.library.edit(f.profile,intro_version=changed["version_id"])
        newconfig=f.library.selection(newer,placement="before")
        service,old,original=outputs[1]
        count=len(f.library.versions(f.profile.channel_id,"intro"))
        rebuilt=asyncio.run(service.build(newconfig))
        assert rebuilt.artifact_id!=old.artifact_id and service.branding.verify_output(old)==old
        assert len(f.library.versions(f.profile.channel_id,"intro"))==count
        assert outputs[0][0].branding.current()==config
        assert f.library.resolve_selection(config)["intro"]["version_id"]==f.intro["version_id"]
        with pytest.raises(RenderCanceled):asyncio.run(service.build(newconfig,canceled=lambda:True))
        assert service.branding.verify_output(rebuilt)==rebuilt
        canceled=[False]
        def stop(phase,*_):
            if phase=="encoding":canceled[0]=True
        with pytest.raises(RenderCanceled):
            asyncio.run(service.build(replace(newconfig,outro=False),canceled=lambda:canceled[0],progress=stop))
        assert service.branding.verify_output(rebuilt)==rebuilt
        for service,_,_ in outputs:
            assert len(service.branding.history("branded_video")) in (1,2)
    finally:
        for s in sessions:s.close()


def test_standard_two_project_acceptance_after_cold_open_and_timed_exports(shared,tmp_path):
    """540s tiny synthetic sources exercise the real guard without two production runs."""
    from app.domain.video_plan import PlannedSection,VideoPlanGroup,VideoPlanRevision,VideoFormat
    from app.domain.narrative_segment import SectionRevision
    from app.domain.plan_script import PlanScriptBinding
    from app.domain.publishing_package import timeline_chapters
    from app.domain.shorts import mapped_interval
    from app.application.timeline import TimelineCompiler,TimelineSceneInput
    from app.application.publishing_package import PublishingPackageService
    from app.application.delivery_export import DeliveryExportService
    from app.storage.delivery import ProjectDelivery
    from app.storage.video_plans import ProjectVideoPlans
    from app.storage.plan_script import ProjectPlanScripts
    from tests.unit.test_timeline import Selected,media
    f=shared
    intro=asyncio.run(f.creator.create_clip(f.profile,"intro",width=640,height=360))
    outro=asyncio.run(f.creator.create_clip(f.profile,"outro",width=640,height=360))
    profile=f.library.edit(f.profile,intro_version=intro["version_id"],outro_version=outro["version_id"])
    config=f.library.selection(profile)
    path=f.root/"standard-source.mp4"
    subprocess.run([str(f.renderer.ffmpeg),"-nostdin","-v","error","-f","lavfi","-i","color=c=red:s=64x36:r=25:d=20",
        "-f","lavfi","-i","color=c=blue:s=64x36:r=25:d=520","-f","lavfi","-i","sine=frequency=220:sample_rate=48000:duration=540",
        "-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0[v]","-map","[v]","-map","2:a","-c:v","libx264","-preset","ultrafast",
        "-threads","2","-pix_fmt","yuv420p","-c:a","aac","-movflags","+faststart",str(path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    subprocess.run([str(f.renderer.ffmpeg),"-nostdin","-v","error","-xerror","-i",str(path),"-f","null","-"],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    measured=asyncio.run(f.renderer.probe(f.root,path.name,lambda:False))
    source_bytes=path.read_bytes();source_hash=sha256(source_bytes).hexdigest()
    worlds=[]
    try:
        for name in ("acceptance-channel-project-A","acceptance-channel-project-B"):
            session=ProjectSession.create(tmp_path/name,repository_factory=ProjectRepository,name=name)
            store=LocalArtifactStore.for_project(session.repository)
            plans=ProjectVideoPlans(session.repository,store);bindings=ProjectPlanScripts(session.repository,store,plans)
            kinds=["cold_open","introduction",*["chapter"]*5,"conclusion"];durations=[20,45,*[84]*5,55]
            groups=tuple(VideoPlanGroup("g"+str(i),kind,kind.title()+" "+str(i),"Synthetic acceptance beat",duration,
                (PlannedSection("s"+str(i),kind.title()+" "+str(i),kind,"Synthetic beat",duration,round(duration*2.2),1),))
                for i,(kind,duration) in enumerate(zip(kinds,durations)))
            plan=VideoPlanRevision("plan",session.project.id,"en",VideoFormat.STANDARD,540,"Fixture","Fixture documentary","Fixture brief","Fixture style",groups)
            plans.save_revision(plan);plans.select(plan,expected_selection_id=None)
            original_script=session.active_script
            sections=tuple(SectionRevision.create_for_plan(project_id=session.project.id,planned_section_id=g.sections[0].id,title=g.title,role=g.kind,text="Synthetic narrative acceptance fixture.") for g in groups)
            script=replace(original_script,id="script_revision_fixture",parent_revision_id=original_script.id,sections=sections)
            session.save_script(script,expected_active_revision_id=original_script.id)
            bindings.save_binding(PlanScriptBinding("binding",session.project.id,plan.id,script.script_id),expected_selection_id=None)
            values=[]
            for i,(section,duration) in enumerate(zip(sections,durations)):
                v=media(str(i),rate=48000,end=duration*48000)
                image=replace(v.image,project_id=session.project.id,section_revision_id=section.id)
                values.append(replace(v,project_id=session.project.id,section_id=section.section_id,section_revision_id=section.id,image=image))
            timeline=TimelineCompiler(Selected(*values)).compile(session.project.id,tuple(TimelineSceneInput(v.timing_id,v.scene_id,"original") for v in values))
            evidence={"fully_decoded":True,"checksum":source_hash,"size_bytes":len(source_bytes),"timeline_id":timeline.id,
                "width":64,"height":36,"fps":25,"frame_count":13500,"sample_rate":48000,"channels":1,
                "video_duration":[540,1],"audio_duration":[measured["audio_duration"].numerator,measured["audio_duration"].denominator]}
            source=store.save_artifact("original.mp4",source_bytes,{"artifact_type":"video_render","project_id":session.project.id,"render":evidence,
                "desktop_dependencies":{"request":{"settings":{"timeline":timeline.to_payload()}}}})
            def resolve(*_,verify_bytes=False,current_source=source,current_store=store):
                assert sha256(current_store.read_artifact(current_source.storage_key)).hexdigest()==current_source.checksum
                return current_source
            renders=NS(selected=resolve,current=lambda t:None,motion_current=lambda _:True)
            timelines=NS(current=lambda t=timeline:NS(timeline=t))
            service=VideoFinishingService(session,ProjectBranding(store,session.project.id),renders,f.renderer,video_plans=plans,plan_scripts=bindings,timelines=timelines,channel_library=f.library)
            branded=asyncio.run(service.build(config))
            assert branded.metadata["finishing"]["frame_count"]==13950
            assert branded.metadata["finishing"]["video_duration"]==[558,1]
            assert branded.metadata["finishing"]["timing_map"]["insertion_frame"]==500
            assert branded.metadata["finishing"]["timing_map"]["insertion_sample"]==960000
            delivery=DeliveryExportService(session,ProjectDelivery(store,session.project.id),renders,None,finishing=service)
            publishing=PublishingPackageService(session,store,renders,timelines,plans,bindings,None,delivery=delivery,finishing=service)
            timing=publishing.timing("branded")
            assert timing.intro==3 and timing.insertion==20 and timing.outro==15
            chapters=timeline_chapters(plan,bindings.active_binding(),script,timeline,timing)
            assert chapters[0]["seconds"]==0 and chapters[1]["seconds"]==23 and chapters[2]["seconds"]==68
            captions=timing.captions((CaptionSegment("a",1,0,19000,"Cold open"),CaptionSegment("b",2,20000,25000,"Introduction"),CaptionSegment("c",3,485000,539000,"Conclusion")))
            assert [c.start_ms for c in captions]==[0,23000,488000] and captions[-1].end_ms==542000
            assert mapped_interval(Fraction(65),Fraction(95),timing,Fraction(25),(Fraction(65),Fraction(95)))==(Fraction(68),Fraction(98))
            assert store.read_artifact(source.storage_key)==source_bytes and session.active_script==script and timelines.current().timeline==timeline
            from app.domain.delivery import DeliveryConfig
            delivery_config=DeliveryConfig(source="branded",preset="source",normalize=False)
            delivered=asyncio.run(delivery.build(delivery_config))
            publishing.context("branded")
            worlds.append((session,service,branded,publishing,source))
        assert len(f.library.versions(profile.channel_id,"intro"))==2
        changed=asyncio.run(f.creator.import_asset(profile.channel_id,"intro",clip(f.renderer,f.root/"acceptance-v2.mp4",seconds=3,color="cyan")))
        profile2=f.library.edit(profile,intro_version=changed["version_id"])
        newer=f.library.selection(profile2)
        b=worlds[1]
        from app.domain.dependencies import content_fingerprint
        from app.domain.delivery import DeliveryConfig
        old_delivery=b[3].delivery.prepare(DeliveryConfig(source="branded",preset="source",normalize=False))[2]
        old_context=b[3].context("branded")[0]
        publishing_count=len(b[1].branding.store.list_artifacts())
        rebuilt=asyncio.run(b[1].build(newer))
        new_delivery=b[3].delivery.prepare(DeliveryConfig(source="branded",preset="source",normalize=False))[2]
        assert new_delivery!=old_delivery and b[3].delivery.artifacts.cached(new_delivery) is None
        assert b[3].delivery.artifacts.cached(old_delivery) is not None
        assert content_fingerprint(b[3].context("branded")[0])!=content_fingerprint(old_context)
        assert not any(m.artifact_type.startswith("publishing_") for m in b[1].branding.store.list_artifacts())
        assert rebuilt.artifact_id!=b[2].artifact_id and b[1].branding.verify_output(b[2])==b[2]
        assert worlds[0][1].branding.current()==config
        assert worlds[0][2].metadata["identity"]["settings"]["intro_asset"]["version_id"]==intro["version_id"]
        assert f.library.path(intro["version_id"]).exists()
        # D073 reads actual inserted durations and only marks non-audio clips as intentional silence.
        from app.application.quality_sources import ProjectQualitySources
        qa=ProjectQualitySources.__new__(ProjectQualitySources)
        a=worlds[0];qa.session=a[0];qa.publishing=a[3]
        original_facts={"stale":False,"shots":[{"start":0.,"end":20.},{"start":20.,"end":540.}],
            "expected":{"fps":25.,"frame_count":13500},"durations":{"actual_narration_seconds":540.}}
        qa._original=lambda m,values:(original_facts,a[3].timelines.current().timeline,None,CompositionTiming(540,540))
        facts,_,_,_=qa._composition(a[2],{a[4].artifact_id:a[4],a[2].artifact_id:a[2]})
        assert facts["durations"]["actual_narration_seconds"]==540. and facts["durations"]["branded_video_seconds"]==558.
        assert facts["branding_intervals"]==[[20.,23.],[543.,558.]]
        assert facts["intentional_silence"]==[[20.,23.],[543.,558.]]
        identities=[(s.repository.workspace,svc.branding.current()) for s,svc,*_ in worlds]
    finally:
        for session,*_ in worlds:session.close()
    reopened_library=ChannelLibrary(f.library.root)
    for workspace,expected in identities:
        with ProjectSession.open(workspace,repository_factory=ProjectRepository) as reopened:
            retained=ProjectBranding(LocalArtifactStore.for_project(reopened.repository),reopened.project.id).current()
            assert retained==expected and reopened_library.resolve_selection(retained)["outro"]["version_id"]==outro["version_id"]


def test_sound_import_and_one_time_music_clip_preserve_audio(shared):
    f=shared
    sound_path=f.root/"sound.wav"
    subprocess.run([str(f.renderer.ffmpeg),"-nostdin","-v","error","-f","lavfi","-i","sine=frequency=990:sample_rate=44100:duration=3",str(sound_path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    sound=asyncio.run(f.creator.import_asset(f.profile.channel_id,"sound",sound_path))
    generated=asyncio.run(f.creator.create_clip(f.profile,"intro",sound_version=sound["version_id"],width=640,height=360))
    assert abs(frequency_at(f.renderer,f.library.path(generated["version_id"]),1)[0]-990)<8
    assert generated["media"]["audio_levels"]["rms_db"] is not None
    with pytest.raises(RenderCanceled):asyncio.run(f.creator.create_clip(f.profile,"intro",sound_version=sound["version_id"],width=640,height=360,canceled=lambda:True))


def test_reusable_prepare_respects_standard_guard_and_missing_boundary(shared,tmp_path):
    f=shared
    session=ProjectSession.create(tmp_path/"blocked",repository_factory=ProjectRepository,name="Blocked")
    try:
        store=LocalArtifactStore.for_project(session.repository)
        source=store.save_artifact("original.mp4",(f.root/"source.mp4").read_bytes(),{"artifact_type":"video_render","project_id":session.project.id,"render":f.evidence})
        service=VideoFinishingService(session,ProjectBranding(store,session.project.id),NS(selected=lambda **_:source),f.renderer,channel_library=f.library)
        config=f.library.selection(f.profile)
        with pytest.raises(ValueError,match="mapping is missing"):service.prepare(config)
        with pytest.raises(ValueError,match="verified frame/sample"):
            asyncio.run(f.renderer.finish(f.root,f.evidence,config,None,clips={},canceled=lambda:False,progress=lambda *_:None))
        service.video_plans=NS(selected=lambda:NS(format="standard"))
        with pytest.raises(ValueError,match="PLAN_DURATION BLOCKED"):service.prepare(config)
        assert service.branding.history("branded_video")==[]
    finally:session.close()


def test_measured_louder_clip_has_actionable_downstream_mastering_warning(shared):
    f=shared
    path=f.root/"loud.mp4"
    subprocess.run([str(f.renderer.ffmpeg),"-nostdin","-v","error","-f","lavfi","-i","color=c=cyan:s=320x180:r=30:d=1",
        "-f","lavfi","-i","sine=frequency=880:sample_rate=44100:duration=1","-af","volume=4","-c:v","libx264","-preset","ultrafast",
        "-threads","2","-pix_fmt","yuv420p","-c:a","aac",str(path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    asset=asyncio.run(f.creator.import_asset(f.profile.channel_id,"intro",path))
    profile=f.library.edit(f.profile,intro_version=asset["version_id"])
    config=f.library.selection(profile,placement="before",outro=False)
    report=asyncio.run(f.renderer.finish(f.root,f.evidence,config,None,clips={"intro":{**asset,"path":f.library.path(asset["version_id"])}},canceled=lambda:False,progress=lambda *_:None))
    assert any("6 dB" in warning and "D069" in warning for warning in report["audio_warnings"])
    assert report["output_audio_levels"]["rms_db"] is not None


def test_rational_source_fps_normalization_retains_exact_frame_sample_map(shared):
    f=shared;root=f.root/"rational-source";root.mkdir()
    subprocess.run([str(f.renderer.ffmpeg),"-nostdin","-v","error","-f","lavfi","-i","color=c=navy:s=160x90:r=30000/1001:d=2.04",
        "-f","lavfi","-i","sine=frequency=220:sample_rate=48000:duration=2.04","-frames:v","61","-c:v","libx264","-threads","2",
        "-pix_fmt","yuv420p","-c:a","aac",str(root/"source.mp4")],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    source=asyncio.run(f.renderer.probe(root,"source.mp4",lambda:False))
    evidence={"fully_decoded":True,"width":160,"height":90,"fps":str(source["fps"]),"frame_count":61,
        "video_duration":[source["video_duration"].numerator,source["video_duration"].denominator],
        "audio_duration":[source["audio_duration"].numerator,source["audio_duration"].denominator]}
    config=f.library.selection(f.profile,placement="before")
    assets=f.library.resolve_selection(config)
    clips={k:{**assets[k],"path":f.library.path(assets[k]["version_id"])} for k in ("intro","outro")}
    result=asyncio.run(f.renderer.finish(root,evidence,config,None,clips=clips,canceled=lambda:False,progress=lambda *_:None))
    assert result["frame_count"]==61+45+30
    assert result["timing_map"]["intro"]["samples"]==72072
    mapped=result["timing_map"]
    original=NS(artifact_id="source",checksum=sha256((root/"source.mp4").read_bytes()).hexdigest(),metadata={"render":evidence})
    manifest=NS(checksum=result["checksum"],metadata={"finishing":result,"identity":{"source_id":"source","source_checksum":original.checksum,"settings":config.to_payload(),"boundary":None}})
    assert verified_timing_map(manifest,original,NS(id="timeline"))==mapped
