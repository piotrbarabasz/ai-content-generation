"""Verified interval ranking recovery; synthetic timelines and offline providers."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction as F
from types import SimpleNamespace as NS

import pytest

from app.domain.publishing_package import CompositionTiming
from app.application.shorts import ShortsService
from app.domain.shorts import ShortAnalysisError, mapped_interval, overlap, ratio
from app.providers.openai_llm import OpenAILLMProvider
from tests.unit.test_shorts import service  # noqa: F401
from tests.unit.test_timeline import compile_values


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_, **__):pytest.fail('Live OpenAI calls are forbidden')
    monkeypatch.setattr(OpenAILLMProvider,'generate_structured',forbidden)


@pytest.fixture
def discovery(service):
    source,evidence,timeline=service.source()
    # Three complete 60s chapters with multiple verified 40/60s alternatives.
    values=[replace(c.media,audio=replace(c.media.audio,end_sample=160000,frame_count=160000))
            for c in timeline.clips]
    timeline=compile_values(*values)
    evidence={**evidence,'frame_count':4500,'video_duration':[180,1],'audio_duration':[180,1]}
    service.publishing.source=lambda kind:(source,evidence,timeline)
    service.publishing.timing=lambda kind:CompositionTiming(F(180),F(180))
    service.publishing.renders.media.boundaries=lambda m:(0,160000)
    return service


def provider(s,choose):
    calls=[]
    pool=s.pool()
    def generate(*_):
        calls.append(1)
        return {'candidates':[{'interval_id':p['id'],'title':f'AI title {i}',
            'reason':f'AI reason {i}','hook_assessment':f'AI assessment {i}','headline':f'AI headline {i}'}
            for i,p in enumerate(choose(pool))]}
    s.publishing.llm=NS(generate_structured=generate)
    return pool,calls


def analyze(s,**kwargs):return s.artifacts.read(asyncio.run(s.analyze(**kwargs)))


@pytest.mark.parametrize('case',['valid','duplicates','two_overlap','three_overlap'])
def test_ranked_preferences_recover_to_exact_count_without_second_request(discovery,case):
    s=discovery
    def choose(pool):
        if case=='valid':return [pool[0],pool[3],pool[6]]
        if case=='duplicates':return [pool[0]]*3
        if case=='two_overlap':return [pool[1],pool[2],pool[3]]
        return pool[:3]
    pool,calls=provider(s,choose)
    before={m.artifact_id:s.store.read_artifact(m.storage_key) for m in s.store.list_artifacts()}
    timeline=deepcopy(s.source()[2].to_payload())
    script={c.media.section_id:s.session.active_script.section(c.media.section_id).text for c in s.source()[2].clips}
    result=analyze(s);selected=result['candidates']
    assert len(calls)==1 and len(selected)==3 and len({p['id'] for p in selected})==3
    assert not any(overlap(a,b) for i,a in enumerate(selected) for b in selected[i+1:])
    assert selected[0]['id']==choose(pool)[0]['id']
    expected_ai={'valid':3,'duplicates':1,'two_overlap':2,'three_overlap':1}[case]
    assert sum(p['selection_provenance']['method']=='llm' for p in selected)==expected_ai
    for item in selected:
        original=next(p for p in pool if p['id']==item['id'])
        assert all(item[k]==original[k] for k in ('start','end','original_start','original_end','text','scene_ids'))
        assert item['status']=='REVIEW' and not s.accepted(item['id'])
        if item['selection_provenance']['method']=='verified_fallback':
            assert all(item[k]==original[k] for k in ('title','reason','hook_assessment','headline'))
            assert 'rank' not in item['selection_provenance']
    assert len({p['group_ids'][0] for p in selected})==3
    reasons={p['reason'] for p in result['discarded_preferences']}
    assert reasons==({'repeated_id'} if case=='duplicates' else
                     {'overlapping_interval'} if 'overlap' in case else set())
    assert all(s.store.read_artifact(m.storage_key)==before[m.artifact_id]
               for m in s.store.list_artifacts() if m.artifact_id in before)
    assert s.candidates()==selected and not s.renderer.calls
    assert s.source()[2].to_payload()==timeline
    assert all(s.session.active_script.section(key).text==text for key,text in script.items())
    with pytest.raises(ValueError,match='Accept the excerpt'):asyncio.run(s.render(selected[-1]['id']))
    assert analyze(s)==result and len(calls)==1


def verified_pool(s, spans):
    template=s.pool()[0];timing=s.publishing.timing('original')
    boundaries=tuple(sorted({F(str(edge)) for a,b,_ in spans for edge in (a,b)}))
    result=[]
    for i,(a,b,group) in enumerate(spans):
        a,b=F(str(a)),F(str(b))
        start,end=mapped_interval(a,b,timing,F(25),boundaries)
        result.append({**template,'id':f'verified-{i}','original_start':ratio(a),'original_end':ratio(b),
            'start':ratio(start),'end':ratio(end),'duration':ratio(end-start),
            'title':f'Heuristic {i}','group_ids':[group]})
    s.pool=lambda *_:deepcopy(result)
    return result


def test_frame_padding_overlap_across_chapters_is_resolved_globally(discovery):
    s=discovery
    pool=verified_pool(s,[(0,30.01,'a'),(30.01,60.02,'b'),(90,130,'c'),(140,180,'d')])
    assert overlap(pool[0],pool[1])>0 and pool[0]['group_ids']!=pool[1]['group_ids']
    _,calls=provider(s,lambda p:p[:3])
    result=analyze(s)
    assert [p['id'] for p in result['candidates']]==[pool[i]['id'] for i in (0,2,3)]
    assert result['discarded_preferences'][0]['reason']=='overlapping_interval' and len(calls)==1


def test_fallback_prefers_different_chapters_over_earlier_same_chapter_interval(discovery):
    s=discovery
    pool=verified_pool(s,[(0,30,'a'),(30,60,'a'),(60,90,'b'),(90,120,'c')])
    provider(s,lambda p:[p[0]]*3)
    assert [p['id'] for p in analyze(s)['candidates']]==[pool[i]['id'] for i in (0,2,3)]


def test_blocking_top_preference_does_not_prevent_a_feasible_exact_count(discovery):
    s=discovery
    pool=verified_pool(s,[(20,60,'a'),(0,30,'a'),(30,60,'b'),(60,90,'c')])
    _,calls=provider(s,lambda p:[p[0]]*3)
    result=analyze(s)
    assert [p['id'] for p in result['candidates']]==[p['id'] for p in pool[1:]]
    assert all(p['selection_provenance']['method']=='verified_fallback' for p in result['candidates'])
    assert result['discarded_preferences'][0]['reason']=='overlap_prevents_completion' and len(calls)==1


@pytest.mark.parametrize('llm',[True,False])
def test_too_few_distinct_verified_intervals_is_actionable_without_provider(discovery,llm):
    s=discovery
    verified_pool(s,[(0,40,'a'),(10,50,'b'),(20,60,'c')])
    calls=[]
    def forbidden(*_):calls.append(1);pytest.fail('Insufficient pool must not call AI')
    s.publishing.llm=NS(generate_structured=forbidden) if llm else None
    with pytest.raises(ShortAnalysisError,match='Only 1.*Lower Candidates') as exc:analyze(s)
    assert exc.value.category=='insufficient_candidates' and not calls
    assert not s.artifacts.history('analysis')


def test_unknown_ids_reject_whole_response_without_substitution_or_losing_history(discovery):
    s=discovery
    provider(s,lambda p:[p[0],p[3],p[6]])
    previous=analyze(s);receipts=s.artifacts.history('analysis_response')
    payload=deepcopy(s.artifacts.read(receipts[0])['response'])
    payload['candidates'][1]['interval_id']='unknown'
    s.publishing.llm=NS(generate_structured=lambda *_:payload)
    with pytest.raises(ShortAnalysisError,match='unknown') as exc:analyze(s,target=46)
    assert exc.value.category=='unknown_candidate_id'
    assert s.artifacts.history('analysis_response')==receipts
    assert s.candidates()==previous['candidates'] and len(s.artifacts.history('analysis'))==1


@pytest.mark.parametrize('value',[None,{}, {'candidates':[]}, {'candidates':[{}]*3},
    {'candidates':[],'authorization':'must-never-be-retained'}])
def test_malformed_responses_are_not_repaired_or_persisted(discovery,value):
    s=discovery;s.publishing.llm=NS(generate_structured=lambda *_:value)
    with pytest.raises(ShortAnalysisError) as exc:analyze(s)
    assert exc.value.category=='invalid_response'
    assert not s.artifacts.history('analysis') and not s.artifacts.history('analysis_response')


def test_valid_receipt_survives_failed_publication_and_reopen_without_second_paid_call(discovery,monkeypatch):
    s=discovery;_,calls=provider(s,lambda p:[p[0]]*3)
    save=s.artifacts.save
    def fail(kind,*args,**kwargs):
        if kind=='analysis':raise OSError('fixture disk failure')
        return save(kind,*args,**kwargs)
    monkeypatch.setattr(s.artifacts,'save',fail)
    with pytest.raises(OSError,match='disk failure'):analyze(s)
    assert not s.candidates() and len(calls)==1
    receipts=s.artifacts.history('analysis_response');assert len(receipts)==1
    evidence=s.artifacts.read(receipts[0])
    assert set(evidence)=={'version','analysis_key','response','status'}
    assert evidence['status']=='UNREVIEWED_SELECTION_EVIDENCE'
    assert all(set(p)=={'interval_id','title','reason','hook_assessment','headline'} for p in evidence['response']['candidates'])
    reopened=ShortsService(s.publishing,s.renderer)
    result=analyze(reopened)
    assert result['response_artifact_id']==receipts[0].artifact_id and len(result['candidates'])==3
    assert len(calls)==1 and len(s.artifacts.history('analysis_response'))==1
    assert analyze(reopened)==result and len(calls)==1


def test_selection_evidence_is_retained_before_final_source_validation(discovery,monkeypatch):
    s=discovery;_,calls=provider(s,lambda p:[p[0]]*3)
    source=s.source;failed=[]
    def changed(kind='original'):
        if s.artifacts.history('analysis_response') and not failed:
            failed.append(True);raise ValueError('Source changed during analysis')
        return source(kind)
    monkeypatch.setattr(s,'source',changed)
    with pytest.raises(ValueError,match='Source changed'):analyze(s)
    assert len(s.artifacts.history('analysis_response'))==1 and not s.candidates()
    assert len(analyze(s)['candidates'])==3 and len(calls)==1


def test_corrupt_selection_receipt_is_rejected_without_dispatch(discovery,monkeypatch):
    s=discovery;_,calls=provider(s,lambda p:[p[0]]*3)
    save=s.artifacts.save
    def fail(kind,*args,**kwargs):
        if kind=='analysis':raise OSError('fixture failure')
        return save(kind,*args,**kwargs)
    monkeypatch.setattr(s.artifacts,'save',fail)
    with pytest.raises(OSError):analyze(s)
    receipt=s.artifacts.history('analysis_response')[0]
    s.store._artifact_path(receipt.storage_key).write_bytes(b'{}')
    with pytest.raises(ValueError,match='checksum'):analyze(s)
    assert len(calls)==1 and not s.candidates()


def test_legacy_success_fingerprint_is_reused_without_new_request(service):
    pool,calls=provider(service,lambda p:p[:3])
    value=analyze(service)
    # Pre-repair successful analyses have no provenance or response reference.
    legacy={k:v for k,v in value.items() if k not in ('discarded_preferences','response_artifact_id')}
    legacy['candidates']=[{k:v for k,v in p.items() if k!='selection_provenance'} for p in value['candidates']]
    manifest=service.artifacts.save('analysis',legacy)
    assert asyncio.run(service.analyze()).artifact_id==manifest.artifact_id and len(calls)==1


@pytest.mark.parametrize('bad',['unknown','structure'])
def test_retained_evidence_is_revalidated_before_replay(discovery,monkeypatch,bad):
    s=discovery;_,calls=provider(s,lambda p:[p[0]]*3)
    save=s.artifacts.save
    def fail(kind,*args,**kwargs):
        if kind=='analysis':raise OSError('fixture failure')
        return save(kind,*args,**kwargs)
    monkeypatch.setattr(s.artifacts,'save',fail)
    with pytest.raises(OSError):analyze(s)
    receipt=s.artifacts.read(s.artifacts.history('analysis_response')[0])
    if bad=='unknown':receipt['response']['candidates'][0]['interval_id']='unknown'
    else:receipt['response']={'candidates':[]}
    save('analysis_response',receipt,analysis_key=receipt['analysis_key'])
    with pytest.raises(ShortAnalysisError) as exc:analyze(s)
    assert exc.value.category==('unknown_candidate_id' if bad=='unknown' else 'invalid_response')
    assert len(calls)==1 and not s.candidates()
