"""Detached analysis reads retain real SQLite ownership and media validation."""
import asyncio
from dataclasses import replace
import pytest

from app.application.publishing_package import PublishingPackageService
from app.application.shorts import ShortsService
from app.domain.plan_script import PlanScriptBinding
from app.storage.plan_script import ProjectPlanScripts
from app.storage.timeline_edits import ProjectTimelineEdits
from app.storage.video_plans import ProjectVideoPlans
from tests.integration.test_section_audio import setup  # noqa: F401
from tests.integration.test_timeline import prepared  # noqa: F401
from tests.integration.test_video_render import render, claim  # noqa: F401
from tests.unit.test_video_plan import standard


@pytest.fixture
def indexed_shorts(setup, render):
    session=setup[0];r=render
    # All generation here uses existing synthetic fixtures and an injected process.
    asyncio.run(r.service.run(claim(r)))
    timelines=ProjectTimelineEdits(session.repository,r.store)
    timelines.save(r.timeline,None)
    plans=ProjectVideoPlans(session.repository,r.store)
    plan=replace(standard(),project_id=session.project.id)
    plans.save_revision(plan);plans.select(plan,expected_selection_id=None)
    scripts=ProjectPlanScripts(session.repository,r.store,plans)
    scripts.save_binding(PlanScriptBinding('binding',session.project.id,plan.id,session.active_script.script_id),
                         expected_selection_id=None)
    publishing=PublishingPackageService(session,r.store,r.adapter,timelines,plans,scripts,None)
    return ShortsService(publishing,None)


def test_detached_source_and_boundary_reads_use_no_live_sqlite(indexed_shorts):
    s=indexed_shorts
    reader,verify=s.analysis_reader('original')
    async def read():
        source,_,timeline=await asyncio.to_thread(reader.source,'original')
        edges=await asyncio.to_thread(reader.boundaries,timeline)
        return source,edges
    source,edges=asyncio.run(read())
    assert source.artifact_type=='video_render' and edges
    assert reader.store is not s.store and reader.session.repository is not s.session.repository
    verify()  # Live owner comparison stays on the calling thread.
    before={m.storage_key:s.store.read_artifact(m.storage_key) for m in s.store.list_artifacts()}
    s.artifacts.save('analysis_response', {'version':1,'analysis_key':'test','response':{'candidates':[]}},
                     analysis_key='test')
    verify()  # Independent response evidence is not a change to source inputs.
    assert all(s.store.read_artifact(key)==value for key,value in before.items())
    assert s.publishing._original_verification==reader.publishing._original_verification
    s.store.save_artifact('change.json','{}',{'artifact_type':'test_change'})
    with pytest.raises(ValueError,match='Project changed'):verify()


def test_detached_source_still_detects_corrupt_mp4(indexed_shorts):
    s=indexed_shorts
    reader,_=s.analysis_reader('original')
    source=s.publishing.renders.selected()
    original=s.store.read_artifact(source.storage_key)
    s.store._artifact_path(source.storage_key).write_bytes(b'x'*len(original))
    with pytest.raises(ValueError,match='checksum'):
        asyncio.run(asyncio.to_thread(reader.source,'original'))
