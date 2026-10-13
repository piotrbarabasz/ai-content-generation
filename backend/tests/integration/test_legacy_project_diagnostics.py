"""Missing historical sources never repair data or conceal current failures."""
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from app.application.history import HistoryService
from app.application.pipeline_diagnostics import PipelineDiagnostics
from app.desktop.timeline_composition import compose_timeline
from app.domain.script import ScriptRevision
from app.storage.history import ProjectHistory
from app.storage.video_render import ProjectVideoRender
from app.storage.delivery import ProjectDelivery
from tests.integration.test_timeline import setup, prepared


def orphan(setup, prepared):
    _, _, _, _, accepted, timing, *_ = prepared
    store=setup[3];revision='section_revision_missing_historical'
    scenes=tuple(replace(s,id='orphan_'+s.id,section_id='historical_section',revision_id=revision) for s in accepted.plan.scenes)
    plan=replace(accepted.plan,id='orphan_plan',section_id='historical_section',revision_id=revision,scenes=scenes)
    acceptance=replace(accepted,id='orphan_acceptance',plan=plan)
    timing=replace(timing,id='orphan_timing',acceptance_id=acceptance.id,plan_id=plan.id,
        scenes=tuple(replace(t,scene_id=s.id) for t,s in zip(timing.scenes,scenes)))
    rows=[]
    for kind,value in [('desktop_scene_plan',plan),('desktop_scene_acceptance',acceptance),('desktop_scene_timing',timing)]:
        rows.append(store.save_artifact(kind+'.json',json.dumps(value.to_payload()),
            {'artifact_type':kind,'project_id':setup[0].project.id,'value_id':value.id,
             'section_id':plan.section_id,'revision_id':revision,'acceptance_id':acceptance.id}))
    return rows,revision


def database_bytes(session):
    root=session.repository.workspace
    return {str(p):sha256(p.read_bytes()).hexdigest() for p in (root/'project.sqlite',root/'jobs.sqlite',root/'artifacts/.artifacts/index.sqlite')}


def test_empty_active_script_with_missing_historical_timing_is_blocked_and_read_only(setup,prepared):
    rows,revision=orphan(setup,prepared);session=setup[0]
    session.save_script(ScriptRevision.create(project_id=session.project.id),expected_active_revision_id=session.active_script.id)
    timeline=compose_timeline(session);before=database_bytes(session);heads=setup[2].selected()
    traces=[];session.repository._connection.set_trace_callback(traces.append)
    report=PipelineDiagnostics().inspect_project(session,timeline=timeline)
    assert report.section_count==0 and not report.ready_for_timeline and report.timeline_expected==0
    assert any(d.stage=='SCRIPT' and d.state=='BLOCKED' for d in report.diagnostics)
    assert any(d.stage=='HISTORY' and revision in d.reason for d in report.diagnostics)
    assert 'History:' in report.summary and revision in report.summary
    assert database_bytes(session)==before and setup[2].selected()==heads
    assert not any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','REPLACE','CREATE','DROP','ALTER')) for s in traces)


def test_valid_current_inputs_survive_unrelated_missing_historical_revision(setup,prepared):
    orphan(setup,prepared);timeline=compose_timeline(setup[0])
    rows=timeline.current_candidate_diagnostics(include_history=True)
    current=[r for r in rows if not r.historical]
    assert len(current)==len(prepared[4].plan.scenes) and all(r.accepted for r in current)
    assert any(r.historical and 'section_revision_missing_historical' in r.reason for r in rows)
    assert timeline.media.plans.proposals(prepared[0].section_id,revision_id=prepared[0].id)
    assert timeline.media.plans.acceptances(prepared[0].section_id,revision_id=prepared[0].id)


def test_unrelated_corrupt_historical_timing_does_not_mask_current(setup,prepared):
    records,_=orphan(setup,prepared)
    setup[3]._artifact_path(records[-1].storage_key).write_bytes(b'{bad historical JSON')
    rows=compose_timeline(setup[0]).current_candidate_diagnostics(include_history=True)
    assert all(r.accepted for r in rows if not r.historical)
    assert any(r.historical and 'checksum' in r.reason.lower() for r in rows)


def test_invalid_current_timing_never_falls_back_to_older_valid_candidate(setup,prepared):
    timeline=compose_timeline(setup[0]);timing=prepared[5]
    m=next(m for m in setup[3].list_artifacts() if m.metadata.get('value_id')==timing.id)
    setup[3]._artifact_path(m.storage_key).write_bytes(b'corrupt current timing')
    rows=timeline.current_candidate_diagnostics()
    assert rows and not any(r.accepted for r in rows if not r.historical)
    report=PipelineDiagnostics().inspect_project(setup[0],timeline=timeline)
    assert report.timeline_rejected and not report.ready_for_timeline
    assert any(d.stage=='TIMELINE' and d.state=='REJECT' for d in report.diagnostics)


def test_retained_candidate_enumeration_reports_each_orphan_without_crashing(setup,prepared):
    orphan(setup,prepared);rows=compose_timeline(setup[0]).candidate_diagnostics()
    assert any(r.accepted for r in rows) and any(r.historical and not r.accepted for r in rows)


def test_healthy_script_history_restores_explicitly_amid_damaged_media(setup,prepared):
    orphan(setup,prepared);session=setup[0];healthy=session.active_script
    session.save_script(ScriptRevision.create(project_id=session.project.id),expected_active_revision_id=healthy.id)
    history=HistoryService(session,ProjectHistory(session.repository));before=session.active_script.id
    choices=history.choices()
    assert session.active_script.id==before
    damaged=[c for c in choices if c.unavailable_reason]
    assert any(c.kind=='desktop_scene_timing' for c in damaged)
    target=next(c for c in choices if c.kind=='script' and c.identity==healthy.id)
    assert not target.unavailable_reason
    with pytest.raises(ValueError,match='unavailable'):history.restore(damaged[0])
    history.restore(target)
    assert session.active_script.sections==healthy.sections


def test_corrupt_image_does_not_hide_other_valid_image_or_script_choices(setup,prepared):
    store=setup[3];bad=prepared[8][0][0]
    m=next(m for m in store.list_artifacts() if m.artifact_id==bad.artifact_id)
    store._artifact_path(m.storage_key).write_bytes(b'bad image')
    choices=ProjectHistory(setup[0].repository).choices(setup[0].active_script)
    assert any(c.kind=='image' and c.identity==bad.artifact_id and c.unavailable_reason for c in choices)
    assert any(c.kind=='image' and not c.unavailable_reason for c in choices)
    assert any(c.kind=='script' and not c.unavailable_reason for c in choices)


def test_missing_audio_source_does_not_hide_healthy_script_history(setup,prepared):
    m=prepared[1];setup[3]._artifact_path(m.storage_key).write_bytes(b'bad retained audio')
    choices=ProjectHistory(setup[0].repository).choices(setup[0].active_script)
    assert any(c.kind=='script' and not c.unavailable_reason for c in choices)
    # Result input checks or restoration still reject corruption; enumeration never regenerates.
    assert any(c.kind=='result' and c.identity==m.artifact_id and c.unavailable_reason for c in choices)
    assert setup[3].read_artifact(m.storage_key)==b'bad retained audio'


def test_final_render_and_delivery_contracts_survive_diagnose_and_history(setup,prepared):
    orphan(setup,prepared);session=setup[0];timeline=compose_timeline(session)
    timeline.rebuild_from_sources([r.source for r in timeline.current_candidate_diagnostics() if r.accepted])
    manifests=[]
    for kind,key in [('video_render','render'),('delivery_video','delivery')]:
        raw=(kind+' verified media fixture').encode()
        manifests.append(setup[3].save_artifact(kind+'.mp4',raw,{'artifact_type':kind,'project_id':session.project.id,
            key:{'checksum':sha256(raw).hexdigest(),'size_bytes':len(raw),'fully_decoded':True,'status':'PASS'}}))
    video,delivery=manifests
    class RetainedRender:
        def selected(self):return video
        def motion_current(self):return True
    session.save_script(ScriptRevision.create(project_id=session.project.id),expected_active_revision_id=session.active_script.id)
    before=database_bytes(session);count=len(setup[3].list_artifacts())
    report=PipelineDiagnostics().inspect_project(session,timeline=timeline,video_render=RetainedRender())
    assert report.final_render_ready and not report.ready_for_timeline and report.section_count==0
    ProjectHistory(session.repository).choices(session.active_script)
    assert ProjectDelivery(setup[3],session.project.id).verify(delivery)==delivery
    assert sha256(setup[3].read_artifact(video.storage_key)).hexdigest()==video.checksum
    assert len(setup[3].list_artifacts())==count and database_bytes(session)==before


def test_unavailable_historical_script_does_not_hide_healthy_snapshots(setup,prepared):
    session=setup[0];c=session.repository._connection
    c.execute('PRAGMA foreign_keys=OFF')
    c.execute('INSERT INTO script_revisions VALUES (?,?,?,?)',('broken_script',session.active_script.script_id,'en',None))
    c.execute('INSERT INTO script_sections VALUES (?,?,?)',('broken_script',0,'missing_section_revision'))
    c.commit()
    service=HistoryService(session,ProjectHistory(session.repository));before=session.active_script
    rows=service.choices()
    broken=next(r for r in rows if r.identity=='broken_script')
    assert broken.unavailable_reason and 'missing_section_revision' in broken.description
    assert any(r.kind=='script' and r.identity==before.id and not r.unavailable_reason for r in rows)
    with pytest.raises(ValueError,match='unavailable'):service.restore(replace(broken,unavailable_reason=None))
    assert session.active_script==before


def test_read_only_integrity_inspector_distinguishes_empty_history_and_missing_source(setup,prepared):
    from app.application.project_integrity import inspect_project
    _,revision=orphan(setup,prepared);session=setup[0];healthy=session.active_script
    session.save_script(ScriptRevision.create(project_id=session.project.id),expected_active_revision_id=healthy.id)
    root=session.repository.workspace;session.close()
    before={str(p):sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.sqlite')}
    r=inspect_project(root,missing_revision=revision)
    assert r['project']['active_section_count']==0 and healthy.id in r['project']['complete_nonempty_script_ids']
    assert not r['project']['missing_revision_exists'] and any(x['kind']=='desktop_scene_timing' for x in r['referencing_artifacts'])
    assert r['database_bytes_unchanged']
    assert before=={str(p):sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.sqlite')}


def test_history_dialog_disables_unavailable_entries_and_keeps_script_choices(setup,prepared):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from app.desktop.history_dialog import HistoryDialog
    app=QApplication.instance() or QApplication([])
    orphan(setup,prepared)
    session=setup[0];healthy=session.active_script
    session.save_script(ScriptRevision.create(project_id=session.project.id),expected_active_revision_id=healthy.id)
    dialog=HistoryDialog(HistoryService(session,ProjectHistory(session.repository)))
    try:
        rows=[dialog.items.item(i) for i in range(dialog.items.count())]
        broken=next(r for r in rows if r.data(Qt.UserRole).unavailable_reason)
        dialog.items.setCurrentItem(broken)
        assert not dialog.restore_button.isEnabled()
        target=next(r for r in rows if r.data(Qt.UserRole).identity==healthy.id)
        dialog.items.setCurrentItem(target)
        assert dialog.restore_button.isEnabled() and session.active_script.id!=healthy.id
    finally:dialog.close()
