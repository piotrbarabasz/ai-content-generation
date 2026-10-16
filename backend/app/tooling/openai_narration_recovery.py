"""Read-only OpenAI narration recovery report. Never opens a writable project session."""
import argparse
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from app.desktop.cloud_audio import CloudAudioServices
from app.domain.generation_job import JobRequest
from app.domain.narration import NarrationSettings
from app.domain.publication import PublicationSnapshot
from app.providers.openai_tts import OpenAITTSProvider
from app.storage.manifest import ArtifactManifest
from app.storage.narration import ProjectNarration
from app.storage.paths import contained_path
from app.storage.project_repository import ProjectRepository


def read_only_scan(workspace, *, job_id=None, provider=None):
    root = Path(workspace).resolve()
    def connect(name):
        db = sqlite3.connect(contained_path(root, name).as_uri() + '?mode=ro', uri=True, timeout=0)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        return db
    with closing(connect('artifacts/.artifacts/index.sqlite')) as db:
        project_id = db.execute('SELECT project_id FROM owner').fetchone()[0]
        manifests = tuple(ArtifactManifest.from_payload(json.loads(r[0])) for r in db.execute('SELECT manifest_json FROM artifacts'))
        heads = dict(db.execute('SELECT output_key, artifact_id FROM d040_heads WHERE artifact_id IS NOT NULL'))
    with closing(connect('jobs.sqlite')) as db:
        if db.execute('SELECT project_id FROM owner').fetchone()[0] != project_id:
            raise ValueError('Job/index project ownership mismatch')
        jobs = tuple(JobRequest.from_payload(json.loads(r[0])) for r in db.execute('SELECT request_json FROM jobs ORDER BY rowid'))
    if job_id:
        job = next(j for j in jobs if j.id == job_id)
        sections = PublicationSnapshot.from_job(job).sections
        config = NarrationSettings.from_payload(json.loads(job.request.settings_json)['voice_config'])
        session = SimpleNamespace(project=SimpleNamespace(id=project_id, language=json.loads(job.request.settings_json)['selection']['language']),
                                  active_script=SimpleNamespace(sections=sections))
    else:
        with closing(connect('project.sqlite')) as db:
            repository = ProjectRepository(root, db)  # constructor only; no open/migration/recovery/transactions
            repository._check_format()
            session = SimpleNamespace(project=repository.project(), active_script=repository.active_script())
        sections = session.active_script.sections
    if session.project.id != project_id or any(s.project_id != project_id for s in sections):
        raise ValueError('Section/project ownership mismatch')
    by_key = {m.storage_key: m for m in manifests}
    def read(key):
        manifest = by_key[key]
        raw = contained_path(root, 'artifacts/' + key).read_bytes()
        if sha256(raw).hexdigest() != manifest.checksum:
            raise ValueError('Indexed artifact checksum mismatch')
        return raw
    store = SimpleNamespace(root=contained_path(root, 'artifacts'), read_artifact=read, list_artifacts=lambda: manifests)
    narration = ProjectNarration.__new__(ProjectNarration)
    narration.session, narration.store = session, store
    narration.records = SimpleNamespace(history=lambda kind: tuple(sorted(
        (m for m in manifests if m.artifact_type == 'narrative_' + kind and m.metadata.get('project_id') == project_id),
        key=lambda m: (m.created_at, m.artifact_id))), read=lambda m: json.loads(read(m.storage_key)))
    service = CloudAudioServices.__new__(CloudAudioServices)
    service.session, service.store, service.narration = session, store, narration
    if job_id:
        service.narration = SimpleNamespace(settings=lambda section_id=None: config, migrated=lambda: True,
            project=narration.project, history=narration.history)
    if provider is None:
        from app.desktop.deployment import media_executables
        provider = OpenAITTSProvider(ffmpeg=media_executables()[0])
    service.provider = provider
    service.root = contained_path(root, 'artifacts/.cloud-narration')  # no mkdir
    service.index = SimpleNamespace(selected=lambda: heads, manifests=lambda: manifests)
    service.coordinator = SimpleNamespace(repository=SimpleNamespace(jobs=lambda: jobs))
    choice = service.choices(session.project.language)[0]
    report = service.recovery_scan(sections, choice)
    # Check all retained selections independently; a frozen-job scan does not claim an active script.
    count = 0
    for key, artifact_id in heads.items():
        if key.endswith(':audio:raw'):
            service.retained_playback(artifact_id)
            count += 1
    report.update(current_script_verified=not bool(job_id), retained_selected_sections_verified=count,
                  scan_scope='frozen_job_only' if job_id else 'current_script', read_only=True)
    if job_id:
        report['summary'] = f'Frozen job only; current script not inspected. Verified retained selected sections: {count}.\n' + report['summary']
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--job-id', help='Inspect an indexed frozen job while the project is open; does not claim current-script provenance')
    args = parser.parse_args(argv)
    from app.environment import load_application_environment
    load_application_environment()
    try:
        report = read_only_scan(args.project, job_id=args.job_id)
    except sqlite3.OperationalError:
        parser.exit(2, 'Project is locked. Close it in the app and rerun the read-only scan; no files were changed.\n')
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 2 if report['blocked'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
