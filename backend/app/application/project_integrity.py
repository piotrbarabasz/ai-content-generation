"""Read-only SQLite/artifact inspection without opening a writable project session.

Run with PYTHONPATH=backend: python -m app.application.project_integrity PROJECT
--output report.json --backup BACKUP_PROJECT. This never selects or repairs data.
"""
from contextlib import closing
from hashlib import file_digest
import json
from pathlib import Path
import sqlite3

from app.domain.timeline import TimelineRevision
from app.storage.paths import contained_path


def digest(path):
    with path.open('rb') as stream: return file_digest(stream,'sha256').hexdigest()


def script_inventory(root, missing_revision):
    with closing(sqlite3.connect((root/'project.sqlite').as_uri()+'?mode=ro',uri=True,timeout=5)) as connection:
        connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        project=dict(connection.execute('SELECT id,active_script_revision_id FROM projects').fetchone())
        scripts=[dict(row) for row in connection.execute('''SELECT r.id,r.script_id,count(s.section_revision_id) AS sections,
            sum(CASE WHEN s.section_revision_id IS NOT NULL AND q.id IS NULL THEN 1 ELSE 0 END) AS missing_sections
            FROM script_revisions r LEFT JOIN script_sections s ON s.script_revision_id=r.id
            LEFT JOIN section_revisions q ON q.id=s.section_revision_id GROUP BY r.id ORDER BY r.rowid''')]
        sections={r[0] for r in connection.execute('SELECT id FROM section_revisions')}
        return {**project,'active_section_count':next(s['sections'] for s in scripts if s['id']==project['active_script_revision_id']),
            'active_section_revision_ids':[r[0] for r in connection.execute('SELECT section_revision_id FROM script_sections WHERE script_revision_id=? ORDER BY position',(project['active_script_revision_id'],))],
            'retained_script_count':len(scripts),'section_revision_count':len(sections),'scripts':scripts,
            'complete_nonempty_script_ids':[s['id'] for s in scripts if s['sections'] and not s['missing_sections']],
            'missing_revision_exists':missing_revision in sections,
            'foreign_key_violations':[list(r) for r in connection.execute('PRAGMA foreign_key_check')]},sections


def inspect_project(root, *, missing_revision, backups=()):
    root=Path(root).resolve(strict=True)
    keys=('project.sqlite','jobs.sqlite','artifacts/.artifacts/index.sqlite')
    before={key:digest(root/key) for key in keys if (root/key).exists()}
    inventory,sections=script_inventory(root,missing_revision)
    with closing(sqlite3.connect((root/'artifacts/.artifacts/index.sqlite').as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
        c.execute('PRAGMA query_only=ON')
        manifests=[json.loads(r[0]) for r in c.execute('SELECT manifest_json FROM artifacts ORDER BY rowid')]
        heads=dict(c.execute('SELECT output_key,artifact_id FROM d040_heads'))
    by_id={m['artifact_id']:m for m in manifests};plans={};payloads={};unavailable=[]
    references=[]
    for m in manifests:
        value=None
        path=contained_path(root/'artifacts',m['storage_key'])
        if path.suffix=='.json':
            try:
                if digest(path)!=m['checksum'] or path.stat().st_size!=m['size_bytes']:raise ValueError('Artifact checksum/size mismatch')
                value=json.loads(path.read_bytes());payloads[m['artifact_id']]=value
                if m['artifact_type']=='desktop_scene_plan':plans[value['id']]=value
            except (OSError,ValueError,KeyError,TypeError) as exc:unavailable.append({'artifact_id':m['artifact_id'],'reason':str(exc)})
        else:value=None
        if missing_revision in json.dumps([m.get('metadata'),value]):
            references.append({'artifact_id':m['artifact_id'],'kind':m['artifact_type'],'value_id':m['metadata'].get('value_id'),'reference':'direct'})
    for m in manifests:
        if m['artifact_type']!='desktop_scene_timing':continue
        value=payloads.get(m['artifact_id'],{});plan=plans.get(value.get('plan_id'),{})
        if plan.get('revision_id')==missing_revision:
            references.append({'artifact_id':m['artifact_id'],'kind':m['artifact_type'],'value_id':m['metadata'].get('value_id'),'reference':'through retained plan '+plan['id']})
    def media(m,key):
        evidence=m['metadata'].get(key,{})
        path=contained_path(root/'artifacts',m['storage_key'])
        verified=path.exists() and path.stat().st_size==m['size_bytes'] and digest(path)==m['checksum']
        contract=(evidence.get('checksum'),evidence.get('size_bytes'))==(m['checksum'],m['size_bytes']) and bool(evidence.get('fully_decoded'))
        return {'artifact_id':m['artifact_id'],'sha256':m['checksum'],'bytes_verified':verified,'retained_contract_valid':bool(verified and contract),
                'width':evidence.get('width'),'height':evidence.get('height'),'frame_count':evidence.get('frame_count')}
    selected=by_id.get(heads.get('project:video_render'))
    timeline_manifests=[m for m in manifests if m['artifact_type']=='desktop_timeline_edit']
    timeline=None
    if timeline_manifests:
        m=max(timeline_manifests,key=lambda m:(m['created_at'],m['artifact_id']))
        try:
            value=TimelineRevision.from_payload(payloads[m['artifact_id']]['timeline'])
            missing=sorted({clip.media.section_revision_id for clip in value.clips}-sections)
            timeline={'artifact_id':m['artifact_id'],'timeline_id':value.id,'retained_payload_valid':True,'missing_source_section_revisions':missing,
                      'current_source_bindings_available':not missing and {clip.media.section_revision_id for clip in value.clips}.issubset(set(inventory['active_section_revision_ids']))}
        except (ValueError,KeyError,TypeError) as exc:timeline={'artifact_id':m['artifact_id'],'retained_payload_valid':False,'reason':str(exc)}
    result={'project':inventory,'missing_revision':missing_revision,'referencing_artifacts':references,
            'selected_video':media(selected,'render') if selected else None,'selected_timeline':timeline,
            'delivery_outputs':[media(m,'delivery') for m in manifests if m['artifact_type']=='delivery_video'],
            'artifact_count':len(manifests),'unavailable_artifacts':unavailable,'backups':[],
            'database_sha256_before':before}
    for backup in backups:
        path=Path(backup).resolve(strict=True);data,_=script_inventory(path,missing_revision)
        result['backups'].append({'path':str(path),'project_sqlite_sha256':digest(path/'project.sqlite'),**data})
    after={key:digest(root/key) for key in before}
    if before!=after:raise ValueError('Project changed during read-only inspection; rerun against a stable copy.')
    result['database_bytes_unchanged']=True
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project');parser.add_argument('--missing-revision',required=True)
    parser.add_argument('--backup',action='append',default=[]);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=inspect_project(args.project,missing_revision=args.missing_revision,backups=args.backup)
    # Explicit separate report, exclusive creation; never overwrite project data.
    with Path(args.output).open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
