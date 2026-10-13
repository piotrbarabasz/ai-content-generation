"""Use the unchanged supervised section-audio contract in an owned derived project."""
import asyncio
from dataclasses import replace
from fractions import Fraction
from hashlib import sha256
import json
from app.application.projects import ProjectSession
from app.domain.base import new_id
from app.domain.dependencies import canonical_json, content_fingerprint
from app.domain.narrative_segment import SectionRevision
from app.domain.section_audio import SectionAudio
from app.runtime.media_process import RenderCanceled
from app.storage.paths import contained_path
from app.storage.project_repository import ProjectRepository
from app.storage.local_store import LocalArtifactStore
from app.storage.result_publication import ResultArtifactIndex
from app.jobs.repository import JobRepository
from app.tts.assembly import inspect_pcm_wav

class DerivedProductionScope:
    def __init__(self,parent,candidate,audio_factory,language=None):
        key=content_fingerprint([parent.project.id,candidate['id'],candidate['source_id'],candidate['source_checksum'],language or parent.project.language])
        self.root=contained_path(parent.repository.workspace,'work/director/'+key[:24])
        self.session=(ProjectSession.open(self.root,repository_factory=ProjectRepository) if (self.root/'project.sqlite').exists()
            else ProjectSession.create(self.root,repository_factory=ProjectRepository,name='Directed Short '+candidate['title'][:60],language=language or parent.project.language))
        try:self.audio=audio_factory(self.session) if audio_factory else None
        except Exception:self.session.close();raise
        marker=self.root/'director-source.json'
        identity={'key':key,'parent_project_id':parent.project.id,'candidate_id':candidate['id'],'source_id':candidate['source_id'],'source_checksum':candidate['source_checksum'],'derived_project_id':self.session.project.id}
        if marker.exists():
            if json.loads(marker.read_text(encoding='utf-8'))!=identity:self.session.close();raise ValueError('Derived workspace lineage mismatch.')
        else:
            with marker.open('x',encoding='utf-8') as target:target.write(canonical_json(identity))
        index=ResultArtifactIndex(self.session.repository,JobRepository(self.session.repository))
        self.store=LocalArtifactStore(index.root,index=index)
    def close(self):self.session.close()
    def choices(self):
        return tuple(sorted(self.audio.choices(self.session.project.language),key=lambda c:(c.provider!='chatterbox_v3',c.label))) if self.audio else ()
    def section(self,text):
        current=self.session.active_script
        if current.sections and current.sections[0].text==text:return current.sections[0]
        section=(replace(current.sections[0],id=new_id('section_revision'),text=text,parent_revision_id=current.sections[0].id)
                 if current.sections else SectionRevision.create(project_id=self.session.project.id,title='Directed narration',text=text,role='narration'))
        revision=replace(current,id=new_id('script_revision'),parent_revision_id=current.id,sections=(section,))
        self.session.save_script(revision,expected_active_revision_id=current.id)
        return section
    async def prepare(self,choice):
        if self.audio is None:raise ValueError('BLOCKED: activate an existing installed TTS provider; no model is downloaded automatically.')
        selection=self.audio.selection(choice);reference=self.audio._resolved_reference(selection)
        prepared=await asyncio.to_thread(self.audio._prepare_voice,selection,120,reference)
        self.audio._prepared_identities[canonical_json(selection)]=prepared['effective_identity']
        return selection,prepared
    def selected(self,section,choice,variant):
        playback=self.audio.playback(section,variant,choice)
        if playback.stale:raise ValueError('STALE: narration belongs to another text/voice revision.')
        suffix='raw' if variant=='original' else 'processed'
        id=self.audio.index.selected().get(f'section:{section.section_id}:audio:{suffix}')
        m=next(m for m in self.audio.store.list_artifacts() if m.artifact_id==id)
        audio=SectionAudio.from_manifest(m);parameters,_=inspect_pcm_wav(playback.payload)
        if sha256(playback.payload).hexdigest()!=audio.checksum or parameters.frame_count!=audio.frame_count:raise ValueError('Invalid measured narration.')
        return m,audio
    async def generate(self,section,choice,*,variant='original',canceled=lambda:False,progress=lambda *_:None):
        if variant not in {'original','processed'}:raise ValueError('Choose original or processed audio explicitly.')
        try:return self.selected(section,choice,variant)
        except (ValueError,StopIteration):
            if variant=='processed':raise ValueError('No current processed derived audio. Choose original; automatic tempo fitting is disabled.')
        self.audio.canceled=False
        task=asyncio.create_task(self.audio.generate(section,choice))
        while not task.done():
            if canceled():self.audio.cancel()
            progress(self.audio.progress(),0,1)
            await asyncio.sleep(.05)
        status=await task
        if canceled():raise RenderCanceled('Narration canceled; completed child artifacts retained.')
        try:return self.selected(section,choice,variant)
        except (ValueError,StopIteration) as exc:raise ValueError('Narration did not complete: '+status) from exc
