import asyncio
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction as F
from hashlib import sha256
from types import SimpleNamespace as NS
import json
import pytest

from app.application.shorts import ShortsService
from app.domain.caption_track import validate_caption_segments
from app.domain.dependencies import content_fingerprint
from app.domain.publishing_package import CompositionTiming
from app.domain.plan_script import planned_section_identity
from app.domain.shorts import ShortStyle, mapped_interval, clip_captions, crop_box, overlays, overlap, ratio
from app.providers.ffmpeg_shorts import FFmpegShortsRenderer
from app.runtime.media_process import RenderCanceled
from app.storage.local_store import LocalArtifactStore
from tests.unit.test_timeline import media, compile_values


class Renderer:
    def __init__(self):self.calls=[];self.failure=False;self.on_render=lambda:None
    def identity(self):return {"adapter":"fixture"}
    async def render(self,root,e,a,b,shots,style,segments,**kwargs):
        self.calls.append((a,b,style));self.on_render()
        if self.failure:raise ValueError("encoder failure")
        raw=b"valid fixture short"+str(style).encode();(root/'short.mp4').write_bytes(raw)
        return {"fully_decoded":True,"status":"READY","source_start":ratio(a),"source_end":ratio(b),
            "width":1080,"height":1920,"sample_rate":48000,"fps":"25","frame_count":int((b-a)*25),
            "video_duration":ratio(b-a),"audio_duration":ratio(b-a),"video_codec":"h264","audio_codec":"aac",
            "checksum":sha256(raw).hexdigest(),"size_bytes":len(raw)}


@pytest.fixture
def service(tmp_path):
    store=LocalArtifactStore(tmp_path/'artifacts')
    sections=[];groups=[];clips=[]
    for i in range(3):
        section_id=planned_section_identity('project',f'planned-{i}')
        sections.append(NS(section_id=section_id,title=f'Topic {i}',text='An independent explanation with a payoff.'))
        groups.append(NS(id=f'group-{i}',kind='chapter',title=f'Topic {i}',sections=[NS(id=f'planned-{i}')]))
        for j in range(3):
            m=media(f'{i}-{j}',end=80000)
            clips.append(replace(m,section_id=section_id))
    timeline=compile_values(*clips)
    raw=b'immutable parent';e={'fully_decoded':True,'width':640,'height':360,'fps':25,'frame_count':2250,
        'video_duration':[90,1],'audio_duration':[90,1]}
    source=store.save_artifact('source.mp4',raw,{'artifact_type':'video_render','project_id':'project','render':e})
    session=NS(project=NS(id='project'),repository=NS(workspace=tmp_path),active_script=NS(script_id='script',section=lambda id:next(s for s in sections if s.section_id==id)))
    pub=NS(store=store,session=session,llm=None,plans=NS(selected=lambda:NS(id='plan',project_id='project',working_title='Parent documentary',format=NS(value='standard'),groups=groups)),
        plan_scripts=NS(active_binding=lambda:NS(video_plan_revision_id='plan',script_id='script')),
        source=lambda kind:(source,e,timeline),timing=lambda kind:CompositionTiming(F(90),F(90)),
        renders=NS(media=NS(boundaries=lambda m:(0,80000),audio=NS(_read=lambda _: (None,NS(speech_boundary_map=None),None)))),
        subtitles=lambda _:(_ for _ in ()).throw(ValueError('MISSING: measured alignment')))
    result=ShortsService(pub,Renderer());result.fixture_source=source
    return result


def analyzed(s):
    return s.artifacts.read(asyncio.run(s.analyze()))['candidates']
def edited(s,p,style=ShortStyle()):
    _,v=s.edit(p['id']);v['style']=style.to_payload();return s.save_edit(v)
def ready(s,p):
    edited(s,p);s.decide(p['id'],True);return asyncio.run(s.render(p['id']))


def test_heuristic_valid_references_distinct_topics_and_no_random_trim(service):
    p=analyzed(service)
    assert len(p)==3 and len({x['group_ids'][0] for x in p})==3
    assert all(F(*x['duration'])==30 and x['status']=='REVIEW' and x['section_ids'] and len(x['scene_ids'])==3 for x in p)
    assert 'Heuristic' in service.artifacts.read(service.artifacts.history('analysis')[0])['origin']

@pytest.mark.parametrize('bad',['unknown','timestamp','duplicate','overlap'])
def test_strict_llm_rejects_invalid_references_fields_or_overlap(service,bad):
    def generate(prompt,schema):
        ids=schema['properties']['candidates']['items']['properties']['interval_id']['enum']
        assert 'timestamps' in prompt and 'section_ids' in prompt
        result=[{'interval_id':x,'title':'Title','hook_assessment':'Review','reason':'Coverage','headline':'Known fact'} for x in ids[:3]]
        if bad=='unknown':result[0]['interval_id']='foreign'
        elif bad=='timestamp':result[0]['start']=45
        else:result[1]['interval_id']=result[0]['interval_id']
        return {'candidates':result}
    service.publishing.llm=NS(generate_structured=generate)
    with pytest.raises(ValueError):asyncio.run(service.analyze())
    assert not service.artifacts.history('analysis')


def test_valid_llm_resolves_timestamps_locally(service):
    def generate(_,schema):
        ids=schema['properties']['candidates']['items']['properties']['interval_id']['enum']
        return {'candidates':[{'interval_id':x,'title':'Title','hook_assessment':'Review','reason':'Coverage','headline':'Known fact'} for x in ids[:3]]}
    service.publishing.llm=NS(generate_structured=generate)
    assert [F(*p['start']) for p in analyzed(service)]==[0,30,60]

@pytest.mark.parametrize('a,b',[(1,40),(0,29),(0,61),(60,30),(-1,40),(60,100)])
def test_only_verified_30_60_boundaries(a,b):
    with pytest.raises(ValueError):mapped_interval(F(a),F(b),CompositionTiming(F(90),F(90)),F(25),tuple(map(F,[0,30,40,60,90])))


def test_piecewise_mapping_and_insertion_end():
    timing=CompositionTiming(F(90),F(94),intro=2,insertion=30,outro=2)
    assert mapped_interval(F(0),F(30),timing,F(25),(F(0),F(30),F(60)))==(0,30)
    assert mapped_interval(F(30),F(60),timing,F(25),(F(0),F(30),F(60)))==(32,62)
    with pytest.raises(ValueError,match='card'):mapped_interval(F(0),F(60),timing,F(25),(F(0),F(30),F(60)))


def test_no_sentence_truncation_frame_padding():
    a,b=mapped_interval(F(1,100),F(4001,100),CompositionTiming(F(90),F(90)),F(25),(F(1,100),F(4001,100)))
    assert a<=F(1,100) and b>=F(4001,100) and b-F(4001,100)<F(1,25)


def test_overlap_and_crop_calculations():
    assert overlap({'start':[0,1],'end':[40,1]},{'start':[30,1],'end':[60,1]})==10
    left,center,right=[crop_box(1920,1080,p) for p in (0,.5,1)]
    assert left[2]==0 and left[0]==606 and center[2]==656 and right[2]==1314
    assert all(x[2]+x[0]<=1920 for x in (left,center,right))


def test_caption_clip_rebase_wording_and_duplicate():
    segments=validate_caption_segments([{'start_ms':29000,'end_ms':31000,'text':'Exact wording.'},{'start_ms':59000,'end_ms':61000,'text':'Payoff.'}])
    result=clip_captions(segments+segments[:1],F(30),F(60))
    assert [(s.start_ms,s.end_ms,s.text) for s in result]==[(0,1000,'Exact wording.'),(29000,30000,'Payoff.')]


def test_missing_alignment_blocks_captioned_edit(service):
    p=analyzed(service)[0]
    with pytest.raises(ValueError,match='alignment'):edited(service,p,ShortStyle(captions=True))
    assert not service.artifacts.history('edit')


def test_safe_overlays_separate_zones_escape_and_two_lines():
    text=overlays(ShortStyle(hook='Known {fact}'),())
    assert '240' in text and '320' in text and r'\{fact\}' in text and r'\fad(150,200)' in text
    with pytest.raises(ValueError,match='two'):overlays(ShortStyle(hook='Many words '*8),())


def test_renderer_fill_fit_and_per_shot_expression():
    r=object.__new__(FFmpegShortsRenderer)
    shots=[{'scene_id':'a','start':[0,1],'end':[10,1]},{'scene_id':'b','start':[10,1],'end':[30,1]}]
    graph=r.filters({'width':1920,'height':1080},shots,ShortStyle(mode='fill',focal_points=(('a',0),('b',1))))
    assert '1314' in graph and 'scale=1080:1920' in graph and 'gte(t,10.0)*lt(t,30.0)' in graph
    assert 'boxblur' in r.filters({},[],ShortStyle())


def test_reuse_reopen_reanalysis_preserve_acceptance_and_parent(service):
    p=analyzed(service)[0]; original=service.store.read_artifact(service.fixture_source.storage_key)
    first=ready(service,p);assert asyncio.run(service.render(p['id'])).artifact_id==first.artifact_id
    service.publishing.store=LocalArtifactStore(service.store.root)
    reopened=ShortsService(service.publishing,service.renderer)
    assert reopened.accepted(p['id']) and reopened.current_output(p['id']).artifact_id==first.artifact_id
    asyncio.run(reopened.analyze());assert reopened.accepted(p['id']) and reopened.edit(p['id'])[0]
    assert len(service.renderer.calls)==1 and service.store.read_artifact(service.fixture_source.storage_key)==original


def test_selective_regeneration_and_fingerprints(service):
    candidates=analyzed(service); videos=[ready(service,p) for p in candidates]
    p=candidates[0];revision,value=service.edit(p['id'])
    value['style']=ShortStyle(mode='fill',focal_points=((p['scene_ids'][0],.2),)).to_payload()
    service.save_edit(value,revision.artifact_id)
    new=asyncio.run(service.render(p['id']))
    assert new.artifact_id!=videos[0].artifact_id
    assert [service.current_output(p['id']).artifact_id for p in candidates[1:]]==[v.artifact_id for v in videos[1:]]
    assert len(service.renderer.calls)==4
    assert new.metadata['fingerprint']==content_fingerprint(new.metadata['identity'])


def test_failure_and_cancel_keep_valid_selection_and_source(service):
    p=analyzed(service)[0];first=ready(service,p);revision,value=service.edit(p['id'])
    value['style']=ShortStyle(hook='Known fact').to_payload();service.save_edit(value,revision.artifact_id)
    service.renderer.failure=True
    with pytest.raises(ValueError):asyncio.run(service.render(p['id']))
    assert service.artifacts.selected(p['id']).artifact_id==first.artifact_id
    service.renderer.failure=False; canceled=[False];service.renderer.on_render=lambda:canceled.__setitem__(0,True)
    with pytest.raises(RenderCanceled):asyncio.run(service.render(p['id'],canceled=lambda:canceled[0]))
    assert service.artifacts.selected(p['id']).artifact_id==first.artifact_id
    assert service.store.read_artifact(service.fixture_source.storage_key)==b'immutable parent'
    assert len(service.artifacts.history('video'))==1


def test_rejected_render_no_artifact_and_stale_source(service):
    p=analyzed(service)[0];edited(service,p)
    with pytest.raises(ValueError,match='Accept'):asyncio.run(service.render(p['id']))
    old=service.publishing.source
    service.publishing.source=lambda kind:(replace(old(kind)[0],checksum='a'*64),*old(kind)[1:])
    with pytest.raises(ValueError,match='STALE'):service.candidate(p['id'])
    assert not service.artifacts.history('video')


def test_invalid_render_report_not_selected(service):
    p=analyzed(service)[0];edited(service,p);service.decide(p['id'],True)
    render=service.renderer.render
    async def invalid(*args,**kwargs):return {**await render(*args,**kwargs),'width':720}
    service.renderer.render=invalid
    with pytest.raises(ValueError,match='invalid'):asyncio.run(service.render(p['id']))
    assert not service.artifacts.history('video') and not service.artifacts.history('selection')


def test_identical_save_reuses_revision_and_render(service):
    p=analyzed(service)[0];first=ready(service,p);revision,value=service.edit(p['id'])
    assert service.save_edit(value,revision.artifact_id).artifact_id==revision.artifact_id
    assert asyncio.run(service.render(p['id'])).artifact_id==first.artifact_id


def test_hook_only_regeneration_keeps_other_short(service):
    a,b=analyzed(service)[:2];v1,v2=ready(service,a),ready(service,b)
    revision,value=service.edit(a['id']);value['style']=ShortStyle(hook='Known fact').to_payload()
    service.save_edit(value,revision.artifact_id)
    assert asyncio.run(service.render(a['id'])).artifact_id!=v1.artifact_id
    assert service.current_output(b['id']).artifact_id==v2.artifact_id


def test_cancel_batch_preserves_completed_short(service):
    candidates=analyzed(service)
    for p in candidates:edited(service,p);service.decide(p['id'],True)
    canceled=[False];original=service.renderer.render
    async def render(*a,**k):
        if len(service.renderer.calls)==1:canceled[0]=True
        return await original(*a,**k)
    service.renderer.render=render
    with pytest.raises(RenderCanceled):asyncio.run(service.render_accepted(canceled=lambda:canceled[0]))
    assert service.artifacts.selected(candidates[0]['id'])
    assert not service.artifacts.selected(candidates[1]['id'])


def test_changed_inputs_during_render_rejects_publication(service):
    p=analyzed(service)[0];edited(service,p);service.decide(p['id'],True)
    service.renderer.on_render=lambda:service.decide(p['id'],False)
    with pytest.raises(ValueError,match='changed'):asyncio.run(service.render(p['id']))
    assert not service.artifacts.history('video')


def test_captioned_render_verified_identity_and_package(service):
    from app.domain.caption_track import SynchronizedCaptionTrack, PublishedCaptionTrack
    segments=validate_caption_segments([{'start_ms':29000,'end_ms':31000,'text':'Original wording.'},{'start_ms':35000,'end_ms':36000,'text':'Payoff.'}])
    content={'version':1,'project_id':'project','timeline_id':service.publishing.source('original')[2].id,
        'language':'en','duration_ms':90000,'alignment_ids':['alignment'],'segments':[vars(x) for x in []],'method':'measured_word_alignment_v1'}
    from dataclasses import asdict
    content['segments']=[asdict(x) for x in segments]
    track=SynchronizedCaptionTrack.from_payload({'id':SynchronizedCaptionTrack.content_id(content),**content})
    published=PublishedCaptionTrack(track,'track','a'*64,'srt','b'*64,'ass','c'*64)
    source=service.fixture_source
    subtitle=service.store.save_artifact('captions.srt','fixture',{'identity':{'track':published.to_payload(),
        'source_id':source.artifact_id,'source_checksum':source.checksum}})
    service.publishing.subtitles=lambda _:subtitle
    p=analyzed(service)[1];edited(service,p,ShortStyle(captions=True,burn=True));service.decide(p['id'],True)
    video=asyncio.run(service.render(p['id']))
    selection=service.artifacts.read(service.artifacts.latest('selection',p['id']))
    srt=service.artifacts.one(selection['srt_id'],'srt')
    text=service.artifacts.records.verified_bytes(srt).decode()
    assert '00:00:00,000 --> 00:00:01,000' in text and 'Original wording.' in text
    assert video.metadata['identity']['caption_identity']['track_id']==track.id
    from io import BytesIO
    import zipfile
    package=service.artifacts.one(selection['package_id'],'package')
    with zipfile.ZipFile(BytesIO(service.artifacts.records.verified_bytes(package))) as z:
        assert z.testzip() is None and set(z.namelist())=={'short-01/short.mp4','short-01/metadata.json','short-01/description.txt','short-01/captions.srt'}
        assert json.loads(z.read('short-01/metadata.json'))['output_checksum']==video.checksum
    wrong=deepcopy(subtitle.metadata);wrong['identity']['source_id']='foreign'
    service.publishing.subtitles=lambda _:replace(subtitle,metadata=wrong)
    with pytest.raises(ValueError,match='Captions'):service.prepare(p,service.edit(p['id'])[1])


def test_corrupt_package_is_never_reused_as_complete_ready_short(service):
    p=analyzed(service)[0];first=ready(service,p)
    selected=service.artifacts.read(service.artifacts.latest('selection',p['id']))
    package=service.artifacts.one(selected['package_id'],'package')
    service.store._artifact_path(package.storage_key).write_bytes(b'damaged fixture')
    with pytest.raises(ValueError):service.current_output(p['id'])
    assert service.artifacts.cached(first.metadata['fingerprint']) is None
    replacement=asyncio.run(service.render(p['id']))
    assert replacement.artifact_id!=first.artifact_id


def test_analyze_cancellation_and_provider_failure_publish_nothing(service):
    with pytest.raises(RenderCanceled):asyncio.run(service.analyze(canceled=lambda:True))
    def fail(*_):raise RuntimeError('provider unavailable')
    service.publishing.llm=NS(generate_structured=fail)
    with pytest.raises(RuntimeError):asyncio.run(service.analyze())
    assert not service.artifacts.history('analysis')


def test_stale_accepted_candidates_can_be_rejected_and_do_not_block_new_batch(service):
    p=analyzed(service)[0];service.decide(p['id'],True)
    old=service.publishing.source
    service.publishing.source=lambda kind:(replace(old(kind)[0],checksum='a'*64),*old(kind)[1:])
    assert asyncio.run(service.render_accepted())==[]
    service.decide(p['id'],False);assert not service.accepted(p['id'])


def test_branding_boundary_frame_ambiguity_fails_closed():
    insertion=F(3001,100)
    timing=CompositionTiming(F(90),F(92),intro=2,insertion=insertion)
    with pytest.raises(ValueError,match='exact source video frame'):
        mapped_interval(F(0),insertion,timing,F(25),(F(0),insertion,F(90)))


def test_probe_decimal_rounding_preserves_exact_fractional_frame_duration(service):
    e=service.publishing.source('original')[1];e['fps']=30;e['frame_count']=2700
    service.publishing.renders.media.boundaries=lambda m:(0,80,80000)
    p=analyzed(service)[0];_,edit=service.edit(p['id']);edit['original_end']=[3001,100]
    service.save_edit(edit);service.decide(p['id'],True)
    original=service.renderer.render
    async def measured(root,e,a,b,*args,**kwargs):
        result=await original(root,e,a,b,*args,**kwargs)
        result.update(fps='30',frame_count=int((b-a)*30),video_duration=ratio(F(str(round(float(b-a),6)))))
        return result
    service.renderer.render=measured
    result=asyncio.run(service.render(p['id']))
    assert result.metadata['shorts']['frame_count']==901
    assert abs(F(*result.metadata['shorts']['video_duration'])-F(901,30))<F(1,1000000)
