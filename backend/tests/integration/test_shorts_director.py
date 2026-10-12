"""Offline real child SQLite/audio-publication pipeline; parent artifacts are immutable."""
import asyncio
from copy import deepcopy
from fractions import Fraction as F
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace as NS
import json
import wave
from PIL import Image
import pytest

from app.application.director_audio import DerivedProductionScope
from app.application.shorts_director import ShortsDirectorService
from app.application.section_audio import SectionAudioService
from app.application.result_publication import ResultPublicationService
from app.desktop.audio_services import AudioChoice,PlaybackAudio
from app.domain.dependencies import canonical_json
from app.domain.shorts import ratio,ShortStyle
from app.domain.section_audio import SectionAudio
from app.jobs.coordinator import JobCoordinator
from app.jobs.repository import JobRepository
from app.providers.image_generation import ImageGenerationCapabilities,ImageGenerationResult
from app.providers.tts_result import TTSSynthesisResult
from app.runtime.section_synthesis import generate,validated_output
from app.runtime.media_process import RenderCanceled
from app.storage.result_publication import ResultArtifactIndex
from app.storage.local_store import LocalArtifactStore
from tests.unit.test_shorts import service as excerpt_fixture,analyzed


class TTS:
    provider_name='mock'
    def __init__(self,identity,owner):self.identity=identity;self.owner=owner
    def effective_synthesis_identity(self,*_):return self.identity
    def synthesize(self,text,config):
        self.owner.calls.append((text,config))
        if self.owner.fail:raise RuntimeError('fixture provider failure')
        seconds=self.owner.seconds
        output=BytesIO()
        with wave.open(output,'wb') as w:
            w.setparams((1,2,8000,0,'NONE','not compressed'));w.writeframes(b'\x01\x00'*int(seconds*8000))
        return TTSSynthesisResult(output.getvalue(),8000,999,'wav','mock')


class Audio:
    providers=('mock',)
    def __init__(self,session):
        self.session=session;self.calls=[];self.fail=False;self.seconds=6;self.canceled=False;self._prepared_identities={}
        jobs=JobRepository(session.repository);self.coordinator=JobCoordinator(jobs)
        self.index=ResultArtifactIndex(session.repository,jobs);self.store=LocalArtifactStore(self.index.root,index=self.index)
        self.work_root=session.repository.workspace.parents[2]/'tt'
        self.outputs=NS(validated=lambda job:validated_output(self.work_root,job))
        self.production=NS(voices=self,outputs=self.outputs)
    def choices(self,language):return tuple(AudioChoice(v,'mock','fixture',v,language) for v in ('one','two'))
    def selection(self,c):return c.selection()|{'settings':{}}
    def _resolved_reference(self,_):return None
    def _prepare_voice(self,selection,words,reference=None):return self.prepare(selection,words)
    def prepare(self,selection,words):return {'selection':selection,'max_words':words,'voice_config':selection,
        'effective_identity':{'synthesis':{'provider':'mock','model':'fixture','voice':{'id':selection['voice'],'catalog':{'expected_sample_rate_hz':8000}}},'runtime':'fixture'}}
    async def generate(self,section,choice):
        prepared=self.prepare(self.selection(choice),120)
        production=SectionAudioService(ResultPublicationService(self.index,self.store),self.coordinator.repository,self,self.outputs)
        attempt=production.enqueue(section,self.selection(choice));claim=self.coordinator.claim_next('fixture')
        job=self.coordinator.repository.get_job(attempt.job_id)
        try:
            await asyncio.to_thread(generate,job,TTS(prepared['effective_identity']['synthesis'],self),self.work_root)
            production.complete(claim);return 'completed'
        except Exception as exc:self.coordinator.fail(claim,str(exc));raise
    def playback(self,section,variant,choice):
        if variant!='original':raise ValueError('processed unavailable')
        id=self.index.selected().get(f'section:{section.section_id}:audio:raw')
        m=next((m for m in self.store.list_artifacts() if m.artifact_id==id),None)
        if m is None:raise ValueError('missing narration')
        audio=SectionAudio.from_manifest(m);selection=m.metadata['desktop_dependencies']['request']['settings']['selection']
        return PlaybackAudio(self.store.read_artifact(m.storage_key),audio.revision_id!=section.id or selection!=self.selection(choice),'fixture')
    def progress(self):return 'Synthesizing fixture'
    def cancel(self):self.canceled=True


class LLM:
    def __init__(self):self.calls=[];self.invalid=False;self.new=False
    def generation_identity(self):return {'provider':'offline-fixture','model':'fixture'}
    def generate_structured(self,prompt,schema):
        self.calls.append(prompt)
        if schema['required']==['hook']:return {'hook':'Imagine networks failing everywhere.'}
        if schema['required']==['scenes']:
            fields=schema['properties']['scenes']['items']['properties'];ids=fields['scene_id']['enum']
            images=fields['asset_ref']['anyOf'][0]['enum']
            return {'scenes':[{'scene_id':id,'asset_ref':None if self.new else images[0],'concept':'Payment resilience','prompt':'Cash in one coherent documentary portrait',
                'rationale':'Verified source cash/terminal coverage supports the spoken point.','hold_reason':''} for id in ids]}
        props=schema['properties'];refs=props['source_refs']['items']['enum']
        parts={'hook':'Imagine the internet disappearing everywhere.',
            'development':'Card terminals may lose authorization. Cash remains an option for payment. Banking apps lose synchronization. Some local services keep operating.',
            'payoff':'Offline backups can help people continue.'}
        text=' '.join(parts.values())
        sentences=['Imagine the internet disappearing everywhere.','Card terminals may lose authorization.','Cash remains an option for payment.',
            'Banking apps lose synchronization.','Some local services keep operating.','Offline backups can help people continue.']
        return {'title':'Money without the internet','language':props['language']['enum'][0],'target_duration':props['target_duration']['enum'][0],
            **parts,'cta':'','narration':text,'style':'documentary','source_refs':['invented'] if self.invalid else refs[:1],
            'claims_review':['Hypothetical outage, source fact review required.'],
            'beats':[{'text':s,'visual_concept':'Cash and terminals','source_refs':refs[:1]} for s in sentences]}


class Images:
    def __init__(self):self.calls=[];self.fail=False
    def capabilities(self):return ImageGenerationCapabilities('fake','portrait','fixture',max_dimension=1024)
    def generate(self,r):
        self.calls.append(r)
        if self.fail:raise RuntimeError('image failure')
        output=BytesIO();Image.new('RGB',(r.width,r.height),'navy').save(output,format=r.format)
        return ImageGenerationResult(output.getvalue(),r.format,r.width,r.height)


class Render:
    def __init__(self):self.calls=[];self.fail=False;self.on_render=lambda:None
    def identity(self):return {'adapter':'fixture-director'}
    async def render(self,root,b,audio,style,segments,**kwargs):
        self.calls.append(b);self.on_render()
        if self.fail:raise ValueError('render failure')
        raw=b'fixture-director-mp4'+canonical_json(b).encode();(root/'director.mp4').write_bytes(raw)
        return {'fully_decoded':True,'width':1080,'height':1920,'sample_rate':48000,'fps':b['fps'],'frame_count':b['scenes'][-1]['end_frame'],
            'audio_duration':ratio(F(audio.frame_count,audio.sample_rate)),'video_duration':ratio(F(b['scenes'][-1]['end_frame'],F(b['fps']))),
            'checksum':sha256(raw).hexdigest(),'size_bytes':len(raw),'video_codec':'h264','audio_codec':'aac'}


@pytest.fixture
def directed(excerpt_fixture,tmp_path):
    shorts=excerpt_fixture;shorts.session.project.language='en';shorts.session.active_script.id='parent-script'
    # Add the exact saved section revision identities/fields used by the verified source port.
    timeline=shorts.publishing.source('original')[2]
    sections=[shorts.session.active_script.section(id) for id in dict.fromkeys(c.media.section_id for c in timeline.clips)]
    for s in sections:s.id='saved-'+s.section_id
    shorts.session.active_script.sections=tuple(sections)
    plan=shorts.publishing.plans.selected();plan.topic='Global internet outage';plan.visual_style='documentary realism';plan.to_payload=lambda:{'id':'plan','format':'standard','topic':plan.topic}
    shorts.publishing.plans.selected=lambda:plan
    llm=LLM();shorts.publishing.llm=None;p=analyzed(shorts)[0];shorts.decide(p['id'],True);shorts.publishing.llm=llm
    output=BytesIO();Image.new('RGB',(640,360),'teal').save(output,format='PNG')
    image=shorts.store.save_artifact('source-image.png',output.getvalue(),{'artifact_type':'scene_image','project_id':'project'})
    def clean(_):return NS(artifact_id=image.artifact_id,checksum=image.checksum,width=640,height=360,provenance='imported',acceptance_id='accepted',scene_id='source-scene')
    scenes=NS(acceptance=lambda _:NS(plan=NS(scenes=[NS(id='source-scene',visual_description='Cash and terminals',source_start=0,source_end=200)])))
    shorts.publishing.renders.media.images=NS(image=clean,scenes=scenes)
    shorts.publishing.artifacts=NS(one=lambda id:next(m for m in shorts.store.list_artifacts() if m.artifact_id==id),bytes=lambda m:shorts.store.read_artifact(m.storage_key))
    def scope(parent,p,lang):return DerivedProductionScope(parent,p,Audio,lang)
    service=ShortsDirectorService(shorts,p['id'],scope,Render(),language='en',image_provider=Images())
    service.fixture=NS(shorts=shorts,source=p,parent_script=deepcopy(shorts.session.active_script.__dict__),llm=llm,scope=scope,image=image)
    yield service
    service.close()


def scripted(s):asyncio.run(s.generate_script());s.approve_script();return s.script()
def voiced(s):
    scripted(s);m=asyncio.run(s.generate_narration(s.scope.choices()[0]));s.approve_narration();return m
def boarded(s):
    voiced(s);return asyncio.run(s.generate_storyboard())


def test_valid_script_ref_grounding_and_invalid_script_never_persists(directed):
    s=directed;s.fixture.llm.invalid=True
    with pytest.raises(ValueError,match='reference'):asyncio.run(s.generate_script())
    assert s.script()==(None,None) and not s.records.history('proposal')
    s.fixture.llm.invalid=False;m,v=scripted(s)
    assert v['narration']==' '.join(v[p] for p in ('hook','development','payoff'))
    assert v['source_refs'][0] in s.context()['source_passages'] and not v['cta']


def test_manual_precedence_and_hook_only_preserves_approved_parts(directed):
    s=directed;m,v=scripted(s);v=deepcopy(v);v['development']='Cash can still pass between people.'
    v['narration']=' '.join(v[p] for p in ('hook','development','payoff'));v['beats']=[{'text':v[p],'visual_concept':'Cash','source_refs':v['source_refs']} for p in ('hook','development','payoff')]
    manual=s.edit_script(v,m.artifact_id);s.approve_script()
    asyncio.run(s.generate_script());assert s.script()[0].artifact_id==manual.artifact_id
    asyncio.run(s.regenerate_hook());new=s.script()[1]
    assert new['development']==v['development'] and new['payoff']==v['payoff']
    assert new['hook']!=v['hook']
    with pytest.raises(ValueError,match='approve'):s.approved_script()


def test_owned_supervised_tts_actual_pcm_and_parent_unchanged(directed):
    s=directed;parent=s.parent.active_script.__dict__.copy();m=voiced(s);n=s.records.read(m)
    assert F(*n['duration'])==36 and n['identity']['effective_identity']['synthesis']['voice']['id']=='one'
    assert s.scope.session.project.id!=s.parent.project.id and n['section_revision_id']!=s.parent.active_script.id
    assert s.parent.active_script.__dict__==parent
    assert s.shorts.store.read_artifact(s.fixture.shorts.fixture_source.storage_key)==b'immutable parent'
    assert len(s.scope.audio.calls)==6
    assert asyncio.run(s.generate_narration(s.scope.choices()[0])).artifact_id==m.artifact_id and len(s.scope.audio.calls)==6

@pytest.mark.parametrize('seconds',[1,11])
def test_measured_duration_blocks_every_visual_call(directed,seconds):
    s=directed;s.scope.audio.seconds=seconds;scripted(s)
    m=asyncio.run(s.generate_narration(s.scope.choices()[0]));assert 'BLOCKED' in s.records.read(m)['status']
    with pytest.raises(ValueError,match='outside'):s.approve_narration()
    with pytest.raises(ValueError):asyncio.run(s.generate_storyboard())
    with pytest.raises(ValueError):asyncio.run(s.generate_missing_images())
    assert not s.image_provider.calls and not s.records.history('storyboard')


def test_reuse_verified_clean_asset_timing_captions_and_cache(directed):
    s=directed;board=boarded(s);b=s.records.read(board)
    assert len(b['scenes'])==6 and b['scenes'][0]['start_frame']==0 and b['scenes'][-1]['end_frame']==900
    assert all(a['end_frame']==b['start_frame'] for a,b in zip(b['scenes'],b['scenes'][1:]))
    assert s.missing_images()[0]==[] and not s.image_provider.calls
    segments,identity=s.captions();assert identity['method']=='verified_sentence_assembly' and identity['audio_id']!=s.fixture.source['source_id']
    assert segments[0].start_ms==0 and segments[-1].end_ms==36000
    first=asyncio.run(s.render());assert asyncio.run(s.render()).artifact_id==first.artifact_id
    selection=s.records.read(s.records.latest('selection'));srt=s.records.bytes(s.records.one(selection['srt_id'],'srt')).decode()
    assert '00:00:00,000 --> 00:00:06,000' in srt and 'Imagine the internet' in srt
    assert len(s.renderer.calls)==1


def test_missing_generation_approval_and_selective_prompt_regeneration(directed):
    s=directed;s.fixture.llm.new=True;boarded(s)
    pending,token=s.missing_images();assert len(pending)==6
    with pytest.raises(ValueError,match='approval'):asyncio.run(s.generate_missing_images())
    assert not s.image_provider.calls
    s.approve_images(token);assert asyncio.run(s.generate_missing_images())==6
    assert asyncio.run(s.generate_missing_images())==0 and len(s.image_provider.calls)==6
    m,b=s.storyboard();b=deepcopy(b);b['scenes'][0]['prompt']='Only this visual changes';s.save_board(b,m.artifact_id)
    pending,token=s.missing_images();assert len(pending)==1;s.approve_images(token)
    asyncio.run(s.generate_missing_images());assert len(s.image_provider.calls)==7 and len(s.scope.audio.calls)==6


def test_script_voice_and_crop_invalidation_are_independent(directed):
    s=directed;boarded(s);first=asyncio.run(s.render());m,b=s.storyboard();b=deepcopy(b);b['scenes'][0]['focal']=.2;s.save_board(b,m.artifact_id)
    assert len(s.scope.audio.calls)==6
    with pytest.raises(ValueError,match='STALE'):s.current_output()
    assert asyncio.run(s.render()).artifact_id!=first.artifact_id
    old_ids=[x['id'] for x in b['scenes']];s.set_voice(s.scope.choices()[1])
    with pytest.raises(ValueError,match='STALE'):s.narration()
    asyncio.run(s.generate_narration(s.scope.choices()[1]));s.approve_narration();asyncio.run(s.generate_storyboard())
    assert [x['id'] for x in s.storyboard()[1]['scenes']]==old_ids and s.storyboard()[1]['scenes'][0]['focal']==.2
    m,v=s.script();v=deepcopy(v);v['title']='New editorial title';s.edit_script(v,m.artifact_id)
    with pytest.raises(ValueError,match='approve'):s.approved_script()
    assert s.records.latest('selection')


def test_reopen_restores_director_and_d071_source_is_unchanged(directed):
    s=directed;boarded(s);first=asyncio.run(s.render());p=s.fixture.source
    scope=s.fixture.scope;shorts=s.shorts;renderer=s.renderer;provider=s.image_provider;s.close()
    reopened=ShortsDirectorService(shorts,p['id'],scope,renderer,language='en',image_provider=provider)
    try:
        assert reopened.current_output().artifact_id==first.artifact_id
        assert asyncio.run(reopened.render()).artifact_id==first.artifact_id
        assert reopened.script()[1]['title']=='Money without the internet'
        assert shorts.candidate(p['id'])==p and shorts.accepted(p['id'])
    finally:reopened.close()
    # Fixture teardown sees an already closed session, which is idempotent.


def test_cancel_failure_and_retry_preserve_completed_stages(directed):
    s=directed;boarded(s);first=asyncio.run(s.render());m,b=s.storyboard();b=deepcopy(b);b['scenes'][0]['focal']=.3;s.save_board(b,m.artifact_id)
    s.renderer.fail=True
    with pytest.raises(ValueError):asyncio.run(s.render())
    assert s.records.read(s.records.latest('selection'))['video_id']==first.artifact_id
    s.renderer.fail=False;canceled=[False];s.renderer.on_render=lambda:canceled.__setitem__(0,True)
    with pytest.raises(RenderCanceled):asyncio.run(s.render(canceled=lambda:canceled[0]))
    assert s.records.read(s.records.latest('selection'))['video_id']==first.artifact_id
    s.renderer.on_render=lambda:None
    assert asyncio.run(s.render()).artifact_id!=first.artifact_id and len(s.scope.audio.calls)==6


def test_invalid_storyboard_refs_and_reordered_timing_are_rejected(directed):
    s=directed;boarded(s);m,b=s.storyboard();b=deepcopy(b);b['scenes'][0]['asset_ref']='foreign'
    with pytest.raises(ValueError):s.save_board(b,m.artifact_id)
    b=s.records.read(m);b['scenes'].reverse()
    with pytest.raises(ValueError):s.save_board(b,m.artifact_id)


def test_generation_failure_and_missing_alignment_never_reach_ready(directed):
    s=directed;scripted(s);s.scope.audio.fail=True
    with pytest.raises(ValueError,match='incomplete'):asyncio.run(s.generate_narration(s.scope.choices()[0]))
    assert not s.records.history('narration') and not s.records.history('video')
    s.scope.audio.fail=False;asyncio.run(s.generate_narration(s.scope.choices()[0]));s.approve_narration()
    raw,audio,_=s.audio_bytes(s.records.read(s.records.latest('narration')))
    from dataclasses import replace
    from app.domain.shorts_director import measured_sentences
    with pytest.raises(ValueError,match='sentence'):measured_sentences(replace(audio,speech_boundary_map=None),s.script()[1]['narration'],F(25))


def test_disabled_image_provider_reuses_completed_generated_assets(directed):
    s=directed;s.fixture.llm.new=True;boarded(s);pending,token=s.missing_images();s.approve_images(token);asyncio.run(s.generate_missing_images())
    s.image_provider=None
    assert s.missing_images()[0]==[] and asyncio.run(s.render())


def test_oversized_static_hold_requires_review(directed):
    s=directed;scripted(s);m,value=s.script();value=deepcopy(value)
    value.update(hook='Imagine internet failure.',development='Cash remains useful.',payoff='Local services may continue.')
    value['narration']=' '.join(value[p] for p in ('hook','development','payoff'))
    value['beats']=[{'text':value[p],'visual_concept':'Cash','source_refs':value['source_refs']} for p in ('hook','development','payoff')]
    s.edit_script(value,m.artifact_id);s.approve_script();s.scope.audio.seconds=11
    asyncio.run(s.generate_narration(s.scope.choices()[0]));s.approve_narration()
    with pytest.raises(ValueError,match='hold'):asyncio.run(s.generate_storyboard())
    assert not s.records.history('storyboard')


def test_hook_regeneration_preserves_reviewed_manual_numbers(directed):
    s=directed;scripted(s);m,value=s.script();value=deepcopy(value)
    value['payoff']='Review this 123 example manually.';value['narration']=' '.join(value[p] for p in ('hook','development','payoff'))
    value['beats']=[{'text':value[p],'visual_concept':'Cash','source_refs':value['source_refs']} for p in ('hook','development','payoff')]
    s.edit_script(value,m.artifact_id);s.approve_script();asyncio.run(s.regenerate_hook())
    assert s.script()[1]['payoff']==value['payoff']


def test_invalid_generation_request_does_not_call_provider(directed):
    for target in (29,61,True):
        with pytest.raises(ValueError):asyncio.run(directed.generate_script(target))
    assert not directed.fixture.llm.calls


def test_voice_round_trip_reuses_the_correct_selected_narration(directed):
    s=directed;first=voiced(s);second=asyncio.run(s.generate_narration(s.scope.choices()[1]));s.approve_narration()
    assert second.artifact_id!=first.artifact_id and len(s.scope.audio.calls)==12
    assert asyncio.run(s.generate_narration(s.scope.choices()[0])).artifact_id==first.artifact_id
    assert s.narration_record().artifact_id==first.artifact_id and len(s.scope.audio.calls)==12


def test_script_cancel_and_source_ownership_require_explicit_candidate_acceptance(directed):
    s=directed
    with pytest.raises(RenderCanceled):asyncio.run(s.generate_script(canceled=lambda:True))
    assert s.script()[0] is None and not s.records.history('proposal')
    s.shorts.decide(s.candidate_id,False)
    with pytest.raises(ValueError,match='Accept'):ShortsDirectorService(s.shorts,s.candidate_id,s.fixture.scope,s.renderer)


def test_image_cancel_preserves_completed_images_and_retry_skips_them(directed):
    s=directed;s.fixture.llm.new=True;boarded(s);_,token=s.missing_images();s.approve_images(token)
    canceled=[False];original=s.image_provider.generate
    def generate(request):
        result=original(request)
        if len(s.image_provider.calls)==2:canceled[0]=True
        return result
    s.image_provider.generate=generate
    with pytest.raises(RenderCanceled):asyncio.run(s.generate_missing_images(canceled=lambda:canceled[0]))
    assert len(s.records.history('image'))==1
    s.image_provider.generate=original;pending,token=s.missing_images();assert len(pending)==5
    s.approve_images(token);asyncio.run(s.generate_missing_images());assert len(s.records.history('image'))==6


def test_processed_audio_never_falls_back_to_original(directed):
    s=directed;voiced(s)
    with pytest.raises(ValueError,match='processed'):asyncio.run(s.generate_narration(s.scope.choices()[0],variant='processed'))


def test_unknown_provider_image_dimensions_fail_before_stage_publication(directed):
    s=directed;s.fixture.llm.new=True;boarded(s);_,token=s.missing_images();s.approve_images(token)
    source=s.image_provider.generate
    def invalid(request):
        from dataclasses import replace
        return replace(source(request),width=10)
    s.image_provider.generate=invalid
    with pytest.raises(ValueError,match='dimensions'):asyncio.run(s.generate_missing_images())
    assert not s.records.history('image')
