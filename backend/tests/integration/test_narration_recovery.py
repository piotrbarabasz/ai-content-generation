"""D079 recovery contracts: isolated temporary projects and offline SDK only."""
import asyncio
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_openai_narration import cloud, SDK, wav, approve
from app.domain.dependencies import content_fingerprint
from app.domain.narration import NarrationSettings
from app.domain.narration_recovery import NarrationPreflightError
from app.desktop.narration_recovery import ATTEMPTS_EXHAUSTED, RECOVERABLE_RESPONSE, VALID_CACHED


def exhaust(service, sdk, sections, choice, *, first_good=False):
    approve(service, sections, choice)
    sdk.failures = ([None] if first_good else []) + [TimeoutError('private network details'), TimeoutError('private network details')]
    with pytest.raises(ValueError, match='SPEECH_TIMEOUT'):
        asyncio.run(service.generate(sections[0], choice))
    return service.recovery_scan(sections, choice)


def blocked_row(report):
    return next(r for r in report['chunk_statuses'] if r['state'] == ATTEMPTS_EXHAUSTED)


def grant(service, report, choice, **kwargs):
    row = blocked_row(report)
    return service.authorize_exceptional_retry(report, choice, scope=row['scope'], chunk_key=row['chunk_key'],
        operator='offline operator', reason='Reviewed failed transport and missing retained response.', budget=.1,
        acknowledge=True, **kwargs)


def restore_response(service, row):
    import base64
    payload = wav()
    folder = service.root/'responses'; folder.mkdir(exist_ok=True)
    (folder/(row['chunk_key']+'.json')).write_text(json.dumps({'version':1,'key':row['chunk_key'],
        'checksum':sha256(payload).hexdigest(),'audio':base64.b64encode(payload).decode()}))


def hashes(root):
    return {p.relative_to(root).as_posix():sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_exhaustion_preflight_is_read_only_and_cannot_approve_again(cloud):
    session,service,sdk,sections=cloud; choice=service.choices('en')[0]
    report=exhaust(service,sdk,sections,choice)
    row=blocked_row(report)
    assert row['api_required'] and len(row['attempts'])==2
    assert all(a['billing']=='UNKNOWN' for a in row['attempts'])
    assert row['category']=='SPEECH_TIMEOUT'
    before=hashes(session.repository.workspace)
    usage=service.narration.history('usage'); approvals=service.narration.history('approval')
    for _ in range(2):
        with pytest.raises(NarrationPreflightError,match='First / sentence-chunk'):
            service.estimate(sections,choice)
        with pytest.raises(NarrationPreflightError):
            asyncio.run(service.generate(sections[0],choice))
    assert hashes(session.repository.workspace)==before
    assert service.narration.history('usage')==usage and service.narration.history('approval')==approvals
    assert len(sdk.calls)==2


def test_preflight_never_opens_payment_dialog_for_blocked_input(cloud,monkeypatch):
    from app.desktop.narration_panel import approve_estimate_async
    _,service,sdk,sections=cloud; choice=service.choices('en')[0]
    report=exhaust(service,sdk,sections,choice)
    def forbidden(*args): raise AssertionError('Ineffective payment dialog must not open')
    monkeypatch.setattr('app.desktop.narration_panel.NarrationCostDialog',forbidden)
    with pytest.raises(NarrationPreflightError):
        asyncio.run(approve_estimate_async(SimpleNamespace(),report))
    assert len(sdk.calls)==2


def test_received_response_resolves_exhaustion_without_fabricating_receipt(cloud):
    _,service,sdk,sections=cloud; choice=service.choices('en')[0]
    report=exhaust(service,sdk,sections,choice)
    row=blocked_row(report); before=service.narration.history('usage')
    restore_response(service,row)
    scan=service.recovery_scan(sections,choice)
    recovered=next(r for r in scan['chunk_statuses'] if r['chunk_key']==row['chunk_key'])
    assert recovered['state']==RECOVERABLE_RESPONSE and not recovered['api_required']
    assert service.narration.history('usage')==before
    assert asyncio.run(service.generate(sections[0],choice))=='completed'
    assert len([r for r in service.narration.history('reservation') if r['chunk_key']==row['chunk_key']])==2
    assert len(sdk.calls)==3  # only its other missing sentence needed a new request
    assert asyncio.run(service.generate(sections[1],choice))=='completed'
    assert service.estimate(sections,choice)['reuse']==2
    assert len(sdk.calls)==4  # the following section resumes independently; its shared sentence is cached


def test_technical_chunks_recover_with_exact_source_and_no_api(cloud):
    _,service,sdk,sections=cloud; choice=service.choices('en')[0]
    report=exhaust(service,sdk,sections,choice,first_good=True)
    for p in (service.root/'responses').glob('*'): p.unlink()
    report=service.recovery_scan(sections,choice)
    assert report['chunk_statuses'][0]['state']==VALID_CACHED
    restore_response(service,blocked_row(report))
    before=len(sdk.calls)
    assert asyncio.run(service.generate(sections[0],choice))=='completed'
    assert len(sdk.calls)==before
    heads=service.index.selected()
    assert asyncio.run(service.generate(sections[0],choice))=='completed'
    assert service.index.selected()==heads and len(sdk.calls)==before


def test_unproven_technical_wav_is_not_recovered(cloud):
    _,service,sdk,sections=cloud; choice=service.choices('en')[0]
    exhaust(service,sdk,sections,choice,first_good=True)
    for p in (service.root/'responses').glob('*'): p.unlink()
    marker=next((service.root/'jobs').glob('*/request.json'))
    value=json.loads(marker.read_text());value['id']='wrong-request'
    marker.write_text(json.dumps(value))
    scan=service.recovery_scan(sections,choice)
    assert scan['chunk_statuses'][0]['state']!='VALID_CACHED'
    assert not any(e['valid'] for e in scan['chunk_statuses'][0]['technical_manifests'])


def test_reviewed_retry_is_bounded_auditable_and_preserves_other_section(cloud):
    session,service,sdk,sections=cloud; choice=service.choices('en')[0]
    approve(service,sections,choice)
    assert asyncio.run(service.generate(sections[1],choice))=='completed'
    retained=service.index.selected().copy(); script=session.active_script
    image=service.store.save_artifact('synthetic-image',b'fixture image',{'artifact_type':'image'})
    image_bytes=service.store.read_artifact(image.storage_key)
    sdk.failures=[TimeoutError(),TimeoutError()]
    with pytest.raises(ValueError): asyncio.run(service.generate(sections[0],choice))
    report=service.recovery_scan(sections,choice); original_approvals=service.narration.history('approval')
    authorization=grant(service,report,choice)
    assert service.narration.history('approval')==original_approvals
    assert authorization['additional_attempts']==1 and len(authorization['prior_reservation_ids'])==2
    assert len(sdk.calls)==4  # second section plus two failed first-section requests
    approve(service,sections,choice)
    assert asyncio.run(service.generate(sections[0],choice))=='completed'
    assert service.index.selected()[f'section:{sections[1].section_id}:audio:raw']==retained[f'section:{sections[1].section_id}:audio:raw']
    assert session.active_script==script and service.store.read_artifact(image.storage_key)==image_bytes
    extra=[r for r in service.narration.history('reservation') if r.get('exceptional_retry_id')]
    assert len(extra)==1 and extra[0]['exceptional_retry_id']==authorization['id']
    before=len(sdk.calls); heads=service.index.selected()
    asyncio.run(service.generate(sections[0],choice))
    assert service.index.selected()==heads and len(sdk.calls)==before


def test_additional_failure_has_no_automatic_retry_or_new_grant(cloud):
    _,service,sdk,sections=cloud; choice=service.choices('en')[0]
    report=exhaust(service,sdk,sections,choice);grant(service,report,choice)
    approve(service,sections,choice)
    error=RuntimeError('private provider body');error.status_code=429;sdk.failures=[error]
    with pytest.raises(ValueError): asyncio.run(service.generate(sections[0],choice))
    assert len(sdk.calls)==3
    report=service.recovery_scan(sections,choice)
    assert len(blocked_row(report)['attempts'])==3
    with pytest.raises(ValueError,match='No exhausted chunk eligible'):
        grant(service,report,choice)


def test_retry_requires_operator_acknowledgement_and_unchanged_evidence(cloud):
    _,service,sdk,sections=cloud; choice=service.choices('en')[0]
    report=exhaust(service,sdk,sections,choice);row=blocked_row(report)
    with pytest.raises(ValueError,match='acknowledgement'):
        service.authorize_exceptional_retry(report,choice,scope=row['scope'],chunk_key=row['chunk_key'],operator='operator',reason='review',budget=.1,acknowledge=False)
    restore_response(service,row)
    with pytest.raises(ValueError,match='evidence changed'):
        grant(service,report,choice)
    assert not service.narration.history('exceptional_retry') and len(sdk.calls)==2


def test_exceptional_authorization_cannot_override_project_ceiling(cloud):
    _,service,sdk,sections=cloud; choice=service.choices('en')[0]
    exhaust(service,sdk,sections,choice)
    used=sum(r['reserved_usd'] for r in service.narration.history('reservation'))
    service.narration.save_project(NarrationSettings(),project_budget=used+.05)
    report=service.recovery_scan(sections,choice)
    with pytest.raises(ValueError,match='Project spending ceiling'):
        grant(service,report,choice)
    assert len(sdk.calls)==2 and not service.narration.history('exceptional_retry')


def test_partial_download_even_decodable_is_not_a_complete_receipt(cloud):
    _,service,sdk,sections=cloud; choice=service.choices('en')[0]
    def broken_stream():
        yield sdk.payload
        raise TimeoutError('private stream details')
    sdk.iter_bytes=broken_stream
    approve(service,sections,choice)
    with pytest.raises(ValueError,match='SPEECH_TIMEOUT'):
        asyncio.run(service.generate(sections[0],choice))
    report=service.recovery_scan(sections,choice)
    row=blocked_row(report)
    assert len(row['responses'])==2 and all(r['category']=='INTERRUPTED_DOWNLOAD' for r in row['responses'])
    diagnostics=[u['diagnostics'] for u in service.narration.history('usage')]
    assert all(d['phase']=='download' and not d['response_complete'] for d in diagnostics)
    assert all('private stream' not in json.dumps(u) for u in service.narration.history('usage'))


def test_application_error_is_not_retried_or_reported_as_network(cloud):
    _,service,sdk,sections=cloud; choice=service.choices('en')[0]
    approve(service,sections,choice);sdk.failures=[RuntimeError('secret must not be logged')]
    with pytest.raises(ValueError,match='APPLICATION_ERROR'):
        asyncio.run(service.generate(sections[0],choice))
    assert len(sdk.calls)==1
    usage=service.narration.history('usage')[-1]
    assert usage['diagnostics']['exception_type']=='RuntimeError' and usage['reservation_id']
    assert usage['billing']=='UNKNOWN' and 'secret must' not in json.dumps(usage)


def test_complete_response_survives_sdk_cleanup_error(cloud):
    _,service,sdk,sections=cloud;choice=service.choices('en')[0]
    approve(service,sections,choice)
    original=SDK.__exit__
    try:
        SDK.__exit__=lambda *args: (_ for _ in ()).throw(RuntimeError('cleanup failed'))
        with pytest.raises(ValueError,match='APPLICATION_ERROR'):
            asyncio.run(service.generate(sections[0],choice))
    finally:
        SDK.__exit__=original
    report=service.recovery_scan(sections,choice)
    assert report['chunk_statuses'][0]['state']==RECOVERABLE_RESPONSE
    usage=service.narration.history('usage')[-1]
    assert usage['diagnostics']['response_complete'] and usage['diagnostics']['phase']=='retention'
    assert asyncio.run(service.generate(sections[0],choice))=='completed'
    assert len(sdk.calls)==2


def test_read_only_tool_does_not_modify_project_or_queue(cloud):
    from app.tooling.openai_narration_recovery import read_only_scan
    session,service,sdk,sections=cloud;choice=service.choices('en')[0]
    exhaust(service,sdk,sections,choice)
    root=session.repository.workspace
    provider=service.provider
    session.close()
    before=hashes(root)
    report=read_only_scan(root,provider=provider)
    assert report['read_only'] and report['current_script_verified'] and report['blocked']
    assert hashes(root)==before and len(sdk.calls)==2


def test_automatic_stops_before_payment_or_voice_with_actual_section(cloud):
    from app.application.automatic_workflow import AutomaticWorkflow,AutomaticWorkflowConfig,AutomaticWorkflowBlocked
    from app.desktop.pipeline_driver import DesktopPipelineDriver
    _,service,sdk,sections=cloud;choice=service.choices('en')[0]
    report=exhaust(service,sdk,sections,choice)
    async def authorize(*_):raise NarrationPreflightError(report)
    desktop=DesktopPipelineDriver.__new__(DesktopPipelineDriver)
    desktop.editor=SimpleNamespace(audio=SimpleNamespace(authorize_sections=authorize))
    class Driver:
        def unsaved_script_draft(self):return False
        def saved_sections(self):return sections
        async def authorize_narration(self,sections,config):await desktop.authorize_narration(sections,config)
        async def generate_voice(self,*_):raise AssertionError('must stop before any generation')
    config=AutomaticWorkflowConfig(choice,None,'landscape','draft')
    with pytest.raises(AutomaticWorkflowBlocked) as caught:
        asyncio.run(AutomaticWorkflow(Driver()).run(config))
    assert caught.value.stage=='VOICE' and caught.value.section=='First'
    assert 'sentence-chunk' in caught.value.reason and len(sdk.calls)==2


def test_cancellation_during_preflight_cannot_start_a_request(cloud,monkeypatch):
    _,service,sdk,sections=cloud;choice=service.choices('en')[0]
    estimate=service.recovery_scan(sections[:1],choice)
    async def cancel_scan(*args):
        service.cancel()
        return estimate
    monkeypatch.setattr(service,'recovery_scan_async',cancel_scan)
    assert asyncio.run(service.generate(sections[0],choice))=='canceled'
    assert not sdk.calls and not service.narration.history('reservation') and not service.index.selected()


def test_exceptional_retry_still_requires_available_operation_budget(cloud):
    _,service,sdk,sections=cloud;choice=service.choices('en')[0]
    report=exhaust(service,sdk,sections,choice);grant(service,report,choice)
    # A separate ordinary ceiling can be lower than the reviewed one; it must still win.
    service.narration.approve([blocked_row(report)['scope']],.001,max_attempts=2)
    with pytest.raises(ValueError,match='budget exhausted'):
        asyncio.run(service.generate(sections[0],choice))
    assert len(sdk.calls)==2 and len(service.narration.history('reservation'))==2


def test_recovery_dialog_requires_review_and_scan_close_is_read_only(cloud):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication,QDialogButtonBox
    from app.desktop.narration_panel import NarrationRecoveryDialog
    session,service,sdk,sections=cloud;choice=service.choices('en')[0]
    report=exhaust(service,sdk,sections,choice)
    app=QApplication.instance() or QApplication([])
    before=hashes(session.repository.workspace)
    dialog=NarrationRecoveryDialog(report)
    button=dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
    assert not button.isEnabled()
    dialog.operator.setText('tester');dialog.reason.setPlainText('Network reviewed')
    assert not button.isEnabled()
    dialog.acknowledge.setChecked(True)
    assert button.isEnabled() and dialog.budget.value()>=blocked_row(report)['estimate']['high_usd']
    dialog.reject();dialog.close()
    assert hashes(session.repository.workspace)==before and len(sdk.calls)==2


def test_early_preflight_blocks_before_plan_context_or_script_calls(cloud):
    from app.application.automatic_workflow import AutomaticWorkflow,AutomaticWorkflowConfig,AutomaticWorkflowBlocked
    from app.desktop.pipeline_driver import DesktopPipelineDriver
    _,service,sdk,sections=cloud;choice=service.choices('en')[0]
    exhaust(service,sdk,sections,choice)
    desktop=DesktopPipelineDriver.__new__(DesktopPipelineDriver)
    desktop.editor=SimpleNamespace(audio=SimpleNamespace(services=service))
    class Driver:
        def unsaved_script_draft(self):return False
        def saved_sections(self):return sections
        async def preflight_narration(self,sections,config):await desktop.preflight_narration(sections,config)
        def selected_video_plan(self):raise AssertionError('No plan/context/script/provider work before blocked preflight')
    with pytest.raises(AutomaticWorkflowBlocked) as caught:
        asyncio.run(AutomaticWorkflow(Driver()).run(AutomaticWorkflowConfig(choice,None,'landscape','draft')))
    assert caught.value.stage=='VOICE' and caught.value.section=='First' and len(sdk.calls)==2
