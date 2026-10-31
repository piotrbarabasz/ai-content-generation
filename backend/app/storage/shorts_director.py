"""Append-only parent-linked director artifacts; no parent head selection."""
from hashlib import file_digest
import json
from app.domain.dependencies import canonical_json
from app.storage.branding import ProjectBranding

class ProjectShortsDirector:
    def __init__(self,store,project_id,candidate_id,language):
        self.store,self.project_id,self.candidate_id=store,project_id,candidate_id
        self.language=language
        self.records=ProjectBranding(store,project_id)
    def history(self,kind):
        return sorted(self.store.find_manifests('shorts_director_'+kind, metadata={
            'project_id':self.project_id,'candidate_id':self.candidate_id,'director_language':self.language}),
            key=lambda m:(m.created_at,m.artifact_id))
    def latest(self,kind):
        values=self.history(kind);return values[-1] if values else None
    def one(self,id,kind):
        m=self.store.manifest_by_id(id)
        if (m is None or m.artifact_type!='shorts_director_'+kind or m.metadata.get('project_id')!=self.project_id
            or m.metadata.get('candidate_id')!=self.candidate_id or m.metadata.get('director_language')!=self.language):
            raise ValueError('Director dependency is missing or belongs to another Short.')
        return m
    def read(self,m):return json.loads(self.records.verified_bytes(m))
    def metadata(self,kind,**extra):
        return {'artifact_type':'shorts_director_'+kind,'module_name':'desktop_shorts_director',
            'project_id':self.project_id,'candidate_id':self.candidate_id,'director_language':self.language,**extra}
    def save(self,kind,value,**extra):return self.store.save_artifact(kind+'.json',canonical_json(value),self.metadata(kind,**extra))
    def bytes(self,m):return self.records.verified_bytes(m)
    def verify_video(self,m):
        m=self.one(m.artifact_id,'video');e=m.metadata['director']
        if not e.get('fully_decoded') or e['checksum']!=m.checksum or e['size_bytes']!=m.size_bytes:raise ValueError('Invalid director decode evidence.')
        with self.store.open_artifact_id(m.artifact_id) as stream:
            if file_digest(stream,'sha256').hexdigest()!=m.checksum:raise ValueError('Director MP4 checksum mismatch.')
        if self.store._artifact_path(m.storage_key).stat().st_size!=m.size_bytes:raise ValueError('Director MP4 size mismatch.')
        return m
    def selection(self,video_id):
        for m in reversed(self.history('selection')):
            value=self.read(m)
            if value['video_id']==video_id:return value
        raise ValueError('Director output has no complete selection.')
    def cached(self,fingerprint):
        for m in reversed(self.history('selection')):
            try:
                selection=self.read(m);video=self.one(selection['video_id'],'video')
                if video.metadata.get('fingerprint')!=fingerprint:continue
                video=self.verify_video(video)
                meta=self.read(self.one(selection['metadata_id'],'metadata'))
                srt=self.one(selection['srt_id'],'srt');self.bytes(srt)
                if (meta['output_checksum']!=video.checksum or meta.get('video_id')!=video.artifact_id
                    or meta.get('identity')!=video.metadata.get('identity') or srt.metadata.get('identity')!=meta['identity'].get('captions')
                    or selection.get('fingerprint')!=video.metadata.get('fingerprint')):continue
                if video.metadata.get('fingerprint')==fingerprint:return video
            except (ValueError,OSError,KeyError):continue
        return None
