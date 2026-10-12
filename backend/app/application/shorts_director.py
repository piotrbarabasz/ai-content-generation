"""Explicit source-grounded production; every stage is separate from the parent film."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction
from hashlib import file_digest,sha256
from io import BytesIO
import json
import shutil
import re
from uuid import uuid4
from PIL import Image

from app.domain.caption_track import serialize_srt
from app.domain.dependencies import canonical_json,content_fingerprint
from app.domain.shorts import ShortStyle,overlays,ratio
from app.domain.shorts_director import PARTS,narration,script_schema,validate_script,measured_sentences,sentence_captions
from app.domain.section_audio import SectionAudio
from app.providers.shorts import DirectorRenderer
from app.providers.image_generation import ImageGenerationRequest,ImageGenerationResult,ImageGenerationCapabilities
from app.runtime.media_process import RenderCanceled
from app.storage.image_decoder import decode_image,ImageLimits
from app.storage.paths import contained_path
from app.storage.shorts_director import ProjectShortsDirector
from app.tts.assembly import inspect_pcm_wav


class ShortsDirectorService:
    def __init__(self,shorts,candidate_id,scope_factory,renderer: DirectorRenderer | None,*,language=None,image_provider=None):
        self.shorts,self.parent,self.renderer=shorts,shorts.session,renderer
        self.language=language or self.parent.project.language
        self.candidate_id=candidate_id;self.image_provider=image_provider
        p=self.source()
        if not shorts.accepted(candidate_id):raise ValueError('Accept the source story before explicitly creating an AI-directed version.')
        self.records=ProjectShortsDirector(shorts.store,self.parent.project.id,candidate_id,self.language)
        self.scope=scope_factory(self.parent,p,self.language)
        self._assets=None
    def close(self):self.scope.close()
    def source(self):return self.shorts.candidate(self.candidate_id)
    def context(self):
        p=self.source();script=self.parent.active_script
        selected=set(p['section_ids']);ids=[s.section_id for s in script.sections]
        positions=[ids.index(id) for id in selected];near={ids[j] for i in positions for j in range(max(0,i-1),min(len(ids),i+2))}
        passages={}
        for s in script.sections:
            if s.section_id not in near:continue
            key='passage_'+content_fingerprint([s.section_id,s.id,s.text])[:24]
            passages[key]={'section_id':s.section_id,'revision_id':s.id,'text':s.text,
                'coverage':'selected story' if s.section_id in selected else 'surrounding context'}
        plan=self.shorts.publishing.plans.selected()
        return {'parent_project_id':self.parent.project.id,'candidate_id':p['id'],'source_id':p['source_id'],
            'source_checksum':p['source_checksum'],'timeline_id':p['timeline_id'],'topic':plan.topic,
            'source_passages':passages,'selected_plan':plan.to_payload(),'language':self.language,'visual_style':plan.visual_style}
    def script(self):
        m=self.records.latest('script');return (m,self.records.read(m)['script']) if m else (None,None)
    def _save_script(self,value,origin,expected,*,generated=False,cta_enabled=False):
        current,_=self.script()
        if (current.artifact_id if current else None)!=expected:raise ValueError('Script changed; reload before saving.')
        context=self.context();validate_script(value,context['source_passages'],self.language,value['target_duration'],generated=generated,cta_enabled=cta_enabled)
        if current and self.records.read(current)['script']==value and self.records.read(current)['context']==context:return current
        return self.records.save('script',{'script':value,'origin':origin,'context':context,'parent_revision_id':expected,'cta_enabled':cta_enabled})
    async def generate_script(self,target=45,style='clear documentary',*,cta=False,canceled=lambda:False):
        if type(target) is not int or not 30<=target<=60 or type(style) is not str or not style.strip() or len(style)>2000 or type(cta) is not bool:raise ValueError('Invalid script generation request.')
        if canceled():raise RenderCanceled('Script generation canceled.')
        provider=self.shorts.publishing.llm
        if provider is None:raise ValueError('Configure a structured LLM; AI Director does not silently substitute an extraction heuristic.')
        context=self.context();current,_=self.script();expected=current.artifact_id if current else None
        prompt=('Write an independently understandable 30-60 second Short in the requested language. Hook 2-5 seconds, concrete development, meaningful payoff. '
            'No greetings, copied long introductions/conclusions, artificial cliffhanger, fabricated statistics, sources or facts. '
            'Distinguish the hypothetical scenario from established facts. Use ONLY supplied exact source passage IDs. No timestamps or artifact IDs. '
            'Aim at about '+str(round(target*self.speaking_rate()))+' words using the available measured speaking rate (otherwise editorial estimate 2.2 words/sec). '
            'Use 5-8 concise editorial beats/sentences, usually <=80 characters per sentence for sentence-level captions. '
            'Beat text must concatenate exactly to full narration; narration must concatenate hook/development/payoff/CTA with single spaces. '
            'Suggested visual concepts must describe one coherent image, no collage, panels, diagrams or written labels. '
            +('A brief CTA is explicitly enabled. ' if cta else 'CTA disabled: return an empty CTA. ')
            +'Style: '+style+'; target: '+str(target)+' seconds. All editorial claims require human review. Context: '+canonical_json(context))
        value=await asyncio.to_thread(provider.generate_structured,prompt,script_schema(context['source_passages'],self.language,target))
        validate_script(value,context['source_passages'],self.language,target,cta_enabled=cta)
        if canceled():raise RenderCanceled('Script generation canceled; working manual text preserved.')
        if self.context()!=context:raise ValueError('Source changed during generation.')
        proposal=self.records.save('proposal',{'script':value,'context':context,'cta_enabled':cta,'origin':'LLM; REVIEW REQUIRED','provider':provider.generation_identity() if hasattr(provider,'generation_identity') else {'provider':provider.provider_name}})
        if current is None:self._save_script(value,'LLM',expected,generated=True,cta_enabled=cta)
        return proposal
    def apply_proposal(self,id,*,replace_current=False):
        proposal=self.records.read(self.records.one(id,'proposal'));current,_=self.script()
        if current and not replace_current:raise ValueError('Explicitly replace the working script; manual revisions have precedence.')
        if proposal['context']!=self.context():raise ValueError('STALE: proposal source changed.')
        return self._save_script(proposal['script'],'explicitly applied LLM proposal',current.artifact_id if current else None,generated=True,cta_enabled=proposal['cta_enabled'])
    def edit_script(self,value,expected):return self._save_script(value,'manual; REVIEW REQUIRED',expected,cta_enabled=bool(value['cta']))
    async def regenerate_hook(self,*,canceled=lambda:False):
        m,value=self.script()
        if m is None:raise ValueError('Create a script first.')
        provider=self.shorts.publishing.llm
        if provider is None:raise ValueError('Configure a structured LLM.')
        context=self.context()
        schema={'type':'object','additionalProperties':False,'required':['hook'],'properties':{'hook':{'type':'string','minLength':1,'maxLength':250}}}
        result=await asyncio.to_thread(provider.generate_structured,'Rewrite ONLY the hook, 2-5 seconds, source-supported facts, no invented numbers. Preserve meaning of development/payoff. '+canonical_json({'script':value,'context':context}),schema)
        if type(result) is not dict or set(result)!={'hook'} or type(result['hook']) is not str or not result['hook'].strip() or len(result['hook'])>250:raise ValueError('Invalid hook output.')
        if canceled():raise RenderCanceled('Hook regeneration canceled.')
        numbers=lambda text:set(re.findall(r'\b\d+(?:[.,]\d+)?%?',text))
        if numbers(result['hook'])-numbers(' '.join(context['source_passages'][r]['text'] for r in value['source_refs'])):raise ValueError('Generated hook introduced unsupported numeric claims.')
        if self.context()!=context:raise ValueError('Source changed during hook generation.')
        new=deepcopy(value);new['hook']=result['hook'];new['narration']=narration(new)
        new['beats']=[{'text':new[p],'visual_concept':new[p],'source_refs':new['source_refs']} for p in PARTS if new[p]]
        return self._save_script(new,'hook-only LLM; other manual parts preserved',m.artifact_id,generated=False,cta_enabled=bool(new['cta']))
    def approve_script(self):
        m,value=self.script()
        if m is None:raise ValueError('No script to review.')
        self.source();return self.records.save('script_approval',{'script_id':m.artifact_id,'approved':True})
    def approved_script(self):
        m,value=self.script();approval=self.records.latest('script_approval')
        if m is None or approval is None or self.records.read(approval)['script_id']!=m.artifact_id:raise ValueError('REVIEW REQUIRED: approve the current script for narration.')
        if self.records.read(m)['context']!=self.context():raise ValueError('STALE: script source changed.')
        return m,value
    def set_voice(self,choice,variant='original'):
        if variant not in {'original','processed'}:raise ValueError('Choose a valid explicit audio variant.')
        value={'selection':self.scope.audio.selection(choice),'variant':variant}
        old=self.records.latest('voice_choice')
        return old if old and self.records.read(old)==value else self.records.save('voice_choice',value)
    def speaking_rate(self):
        selected=self.records.latest('voice_choice');selected=self.records.read(selected) if selected else None
        for m in reversed(self.records.history('narration')):
            value=self.records.read(m)
            if selected and value['identity']['selection']!=selected['selection']:continue
            script=self.records.read(self.records.one(value['script_id'],'script'))['script']
            return len(script['narration'].split())/float(Fraction(*value['duration']))
        return 2.2
    def audio_bytes(self,value):
        m=next((m for m in self.scope.store.list_artifacts() if m.artifact_id==value['audio_id']),None)
        if m is None:raise ValueError('Missing owned derived narration.')
        raw=self.scope.store.read_artifact(m.storage_key);audio=SectionAudio.from_manifest(m)
        parameters,_=inspect_pcm_wav(raw)
        if (audio.checksum!=value['checksum'] or sha256(raw).hexdigest()!=value['checksum'] or parameters.frame_count!=audio.frame_count
                or parameters.sample_rate!=audio.sample_rate or audio.section_id!=value['section_id'] or audio.revision_id!=value['section_revision_id']):raise ValueError('Narration identity or PCM measurements changed.')
        return raw,audio,m
    async def generate_narration(self,choice,*,variant='original',canceled=lambda:False,progress=lambda *_:None):
        self.set_voice(choice,variant)
        m,value=self.approved_script();section=self.scope.section(value['narration'])
        selection,prepared=await self.scope.prepare(choice)
        identity={'script_id':m.artifact_id,'selection':selection,'effective_identity':prepared['effective_identity'],'variant':variant,
            'section_revision_id':section.id,'derived_project_id':self.scope.session.project.id}
        fingerprint=content_fingerprint(identity)
        for old in reversed(self.records.history('narration')):
            if old.metadata.get('fingerprint')==fingerprint:
                try:
                    self.audio_bytes(self.records.read(old));self.records.save('narration_selection',{'narration_id':old.artifact_id});return old
                except (ValueError,OSError):continue
        if canceled():raise RenderCanceled('Narration canceled before provider execution.')
        manifest,audio=await self.scope.generate(section,choice,variant=variant,canceled=canceled,progress=progress)
        if canceled():raise RenderCanceled('Narration canceled before stage publication.')
        if self.approved_script()[0].artifact_id!=m.artifact_id:raise ValueError('Script changed during narration.')
        if audio.section_id!=section.section_id or audio.revision_id!=section.id or audio.checksum!=manifest.checksum:raise ValueError('Derived narration belongs to another section/provider result.')
        duration=Fraction(audio.frame_count,audio.sample_rate)
        retained=self.records.save('narration',{'script_id':m.artifact_id,'identity':identity,'audio_id':manifest.artifact_id,'checksum':audio.checksum,
            'section_id':section.section_id,'section_revision_id':section.id,'duration':ratio(duration),'target':value['target_duration'],
            'deviation_seconds':float(duration-value['target_duration']),'status':'REVIEW REQUIRED' if 30<=duration<=60 else 'BLOCKED: revise script; duration outside 30-60 seconds'},fingerprint=fingerprint)
        self.records.save('narration_selection',{'narration_id':retained.artifact_id})
        return retained
    def narration_record(self):
        selection=self.records.latest('narration_selection')
        return self.records.one(self.records.read(selection)['narration_id'],'narration') if selection else self.records.latest('narration')
    def narration(self,*,approved=False):
        script,value=self.approved_script();m=self.narration_record()
        if m is None:raise ValueError('BLOCKED: generate derived narration.')
        n=self.records.read(m)
        voice=self.records.latest('voice_choice')
        if n['script_id']!=script.artifact_id or voice and self.records.read(voice)!={'selection':n['identity']['selection'],'variant':n['identity']['variant']}:raise ValueError('NARRATION STALE: script or selected voice changed.')
        raw,audio,manifest=self.audio_bytes(n)
        if not 30<=Fraction(audio.frame_count,audio.sample_rate)<=60:raise ValueError(n['status']+f"; actual {float(Fraction(*n['duration'])):.3f}s, deviation {n['deviation_seconds']:+.3f}s")
        if approved:
            a=self.records.latest('narration_approval')
            if a is None or self.records.read(a)['narration_id']!=m.artifact_id:raise ValueError('REVIEW REQUIRED: listen and approve current narration before visuals.')
        return m,n,raw,audio
    def approve_narration(self):
        m,*_=self.narration();return self.records.save('narration_approval',{'narration_id':m.artifact_id,'approved':True})
    def assets(self):
        p=self.source()
        if self._assets is not None:return deepcopy(self._assets)
        _,_,timeline=self.shorts.source(p['source_kind']);images=self.shorts.publishing.renders.media.images
        result={}
        for c in timeline.clips:
            image=images.image(c.media.image.artifact_id)
            while image.provenance=='motion_master':image=images.image(image.source_artifact_id)
            accepted=images.scenes.acceptance(image.acceptance_id)
            scene=next(s for s in accepted.plan.scenes if s.id==image.scene_id)
            section=self.parent.active_script.section(c.media.section_id)
            result[image.artifact_id]={'artifact_id':image.artifact_id,'checksum':image.checksum,'width':image.width,'height':image.height,
                'scene_id':image.scene_id,'section_id':c.media.section_id,'description':scene.visual_description,
                'source_passage':section.text[scene.source_start:scene.source_end],'priority':0 if c.media.section_id in p['section_ids'] else 1}
        self._assets=dict(sorted(result.items(),key=lambda item:item[1]['priority']))
        return deepcopy(self._assets)
    def sentences(self):
        m,n,raw,audio=self.narration(approved=True);_,script=self.approved_script()
        _,e,_=self.shorts.source(self.source()['source_kind']);fps=Fraction(str(e['fps']))
        return m,n,audio,measured_sentences(audio,script['narration'],fps),fps
    def storyboard(self):
        m=self.records.latest('storyboard')
        if m is None:raise ValueError('BLOCKED: generate/review the new storyboard.')
        b=self.records.read(m);n,_,_,_,_=self.sentences()
        if b['narration_id']!=n.artifact_id:raise ValueError('STALE: storyboard narration changed.')
        self.validate_board(b)
        return m,b
    def validate_board(self,b):
        n,_,audio,sentences,fps=self.sentences();pool=self.assets()
        if type(b) is not dict or set(b)!={'narration_id','fps','scenes'} or b['narration_id']!=n.artifact_id or b['fps']!=str(fps) or len(b['scenes'])!=len(sentences):raise ValueError('Invalid current storyboard coverage.')
        for actual,expected in zip(b['scenes'],sentences):
            if set(actual)!=set(expected)|{'concept','prompt','asset_ref','rationale','hold_reason','mode','focal','zoom','pan'} or any(actual[k]!=v for k,v in expected.items()):raise ValueError('Storyboard must retain exact measured sentence timing and order.')
            if actual['asset_ref'] is not None and actual['asset_ref'] not in pool:raise ValueError('Unknown, stale or unrelated visual artifact.')
            if any(type(actual[k]) is not str or not actual[k].strip() or len(actual[k])>2000 for k in ('concept','prompt','rationale')):raise ValueError('Provide a visual concept, prompt and editorial reuse justification.')
            if type(actual['hold_reason']) is not str or (expected['end_frame']-expected['start_frame'])/fps>10 and not actual['hold_reason'].strip():raise ValueError('Review and justify a scene hold longer than ten seconds.')
            if actual['zoom'] not in {'off','subtle'} or actual['pan'] not in {'off','subtle'}:raise ValueError('Only restrained single-pass motion is supported.')
            ShortStyle(mode=actual['mode'],focal_points=((actual['id'],actual['focal']),))
        return b
    async def generate_storyboard(self,*,canceled=lambda:False):
        n,_,audio,sentences,fps=self.sentences();assets=self.assets();provider=self.shorts.publishing.llm
        previous=self.records.latest('storyboard')
        if previous:
            old=self.records.read(previous);by_id={s['id']:s for s in old['scenes']}
            if all(s['id'] in by_id and by_id[s['id']]['text']==s['text'] for s in sentences) and len(sentences)==len(old['scenes']):
                retimed={'narration_id':n.artifact_id,'fps':str(fps),'scenes':[{**by_id[s['id']],**s} for s in sentences]}
                self.validate_board(retimed)
                return previous if old==retimed else self.records.save('storyboard',retimed,origin='retimed; retained visual edits')
        if provider is None:raise ValueError('Configure a structured visual director LLM.')
        fields={'scene_id':{'type':'string','enum':[s['id'] for s in sentences]},'asset_ref':{'anyOf':[{'type':'string','enum':list(assets)},{'type':'null'}]},
            **{k:{'type':'string','maxLength':2000} for k in ('concept','prompt','rationale','hold_reason')}}
        schema={'type':'object','additionalProperties':False,'required':['scenes'],'properties':{'scenes':{'type':'array','minItems':len(sentences),'maxItems':len(sentences),
            'items':{'type':'object','additionalProperties':False,'required':list(fields),'properties':fields}}}}
        prompt=('Direct visuals for the NEW rewritten speech, not the old documentary edit. Return each supplied sentence scene ID exactly once, in order. '
            'Use a relevant clean verified original/project image when suitable; choose null only if missing. Justify relevance and framing. Never choose by nearest timestamp. '
            'No invented image/scene IDs or timestamps. One coherent photographic composition per scene, portrait safe subject placement, no collages, panels, diagrams, labels or text. '
            'Keep source style: '+self.context()['visual_style']+'. Justify any >10 second hold. Sentence timing is measured, not editable. '
            +canonical_json({'new_script':self.approved_script()[1],'measured_sentences':sentences,'verified_assets':assets}))
        value=await asyncio.to_thread(provider.generate_structured,prompt,schema)
        if type(value) is not dict or set(value)!={'scenes'} or type(value['scenes']) is not list or len(value['scenes'])!=len(sentences):raise ValueError('Invalid structured visual direction.')
        b={'narration_id':n.artifact_id,'fps':str(fps),'scenes':[]}
        for v,s in zip(value['scenes'],sentences):
            if type(v) is not dict or set(v)!=set(fields) or v['scene_id']!=s['id']:raise ValueError('Invented/reordered/duplicated storyboard scene.')
            b['scenes'].append({**s,**{k:v[k] for k in fields if k!='scene_id'},'mode':'fit','focal':.5,'zoom':'subtle','pan':'off'})
        if canceled():raise RenderCanceled('Storyboard canceled.')
        self.validate_board(b);return self.records.save('storyboard',b,origin='LLM; visual review required')
    def save_board(self,b,expected):
        if self.records.latest('storyboard').artifact_id!=expected:raise ValueError('Storyboard changed; reload.')
        self.validate_board(b);return self.records.save('storyboard',b,origin='LLM; visual review required')
    def image_request(self,scene):
        provider=self.image_provider
        if provider is None:raise ValueError('BLOCKED: configure an image provider or select an existing relevant image.')
        caps=provider.capabilities()
        if not isinstance(caps,ImageGenerationCapabilities):raise ValueError('Invalid image provider capabilities.')
        sizes=[s for s in caps.supported_sizes if s[1]>=s[0]]
        if caps.supported_sizes:width,height=min(sizes or caps.supported_sizes,key=lambda s:s[0]*s[1])
        else:
            scale=min(1,caps.max_dimension/768,(caps.max_pixels/(512*768))**.5)
            width,height=max(1,int(512*scale)),max(1,int(768*scale))
        request=ImageGenerationRequest(scene['prompt']+' Single coherent portrait composition, no collage, panels, diagram or written text. Visual style: '+self.context()['visual_style'],width,height,format=caps.formats[0])
        caps.validate(request)
        identity={'scene_id':scene['id'],'request':request.to_payload(),'provider':caps.to_payload(),'visual_style':self.context()['visual_style']}
        return request,identity,content_fingerprint(identity)
    def image(self,scene,*,missing_ok=False):
        if scene['asset_ref']:
            assets=self.assets();asset=assets.get(scene['asset_ref'])
            if asset is None:raise ValueError('Stale reused image.')
            m=self.shorts.publishing.artifacts.one(asset['artifact_id']);raw=self.shorts.publishing.artifacts.bytes(m)
            decode_image(raw,ImageLimits(max_bytes=64*1024*1024))
            if m.checksum!=asset['checksum']:raise ValueError('Reused image checksum changed.')
            return m,raw,{'kind':'reused','source_id':m.artifact_id,'source_checksum':m.checksum,'relevance':scene['rationale']}
        if self.image_provider is None:
            prompt=scene['prompt']+' Single coherent portrait composition, no collage, panels, diagram or written text. Visual style: '+self.context()['visual_style']
            for m in reversed(self.records.history('image')):
                saved=m.metadata.get('identity',{})
                if saved.get('scene_id')==scene['id'] and saved.get('request',{}).get('prompt')==prompt:
                    raw=self.records.bytes(m);decode_image(raw,ImageLimits())
                    return m,raw,{'kind':'generated','generation':saved}
            if missing_ok:return None
        request,identity,fingerprint=self.image_request(scene)
        for m in reversed(self.records.history('image')):
            if m.metadata.get('fingerprint')==fingerprint:
                try:
                    raw=self.records.bytes(m);decode_image(raw,ImageLimits());return m,raw,{'kind':'generated','generation':identity}
                except (ValueError,OSError):continue
        if missing_ok:return None
        raise ValueError('BLOCKED: approve and generate the missing image for '+scene['id'])
    def missing_images(self):
        _,b=self.storyboard();pending=[]
        for s in b['scenes']:
            if self.image(s,missing_ok=True) is None:
                request,identity,fingerprint=self.image_request(s)
                pending.append({'scene_id':s['id'],'identity':identity,'fingerprint':fingerprint})
        return pending,content_fingerprint(pending)
    def approve_images(self,token):
        pending,current=self.missing_images()
        if token!=current:raise ValueError('Image requests changed; review count/provider again.')
        return self.records.save('image_approval',{'token':token,'count':len(pending),'requests':pending,'approved':True})
    async def generate_missing_images(self,*,canceled=lambda:False,progress=lambda *_:None):
        board,b=self.storyboard();pending,token=self.missing_images();approval=self.records.latest('image_approval')
        if pending and (approval is None or self.records.read(approval)['token']!=token):raise ValueError('Explicit approval of the current image count/provider/model is required.')
        for i,item in enumerate(pending):
            if canceled():raise RenderCanceled('Image generation canceled; earlier completed images retained.')
            if self.storyboard()[0].artifact_id!=board.artifact_id:raise ValueError('Storyboard changed during image generation.')
            s=next(s for s in b['scenes'] if s['id']==item['scene_id']);request,identity,fingerprint=self.image_request(s)
            if fingerprint!=item['fingerprint']:raise ValueError('Image provider/request changed after approval.')
            progress('Generating image',i,len(pending));result=await asyncio.to_thread(self.image_provider.generate,request)
            if canceled():raise RenderCanceled('Image generation canceled before publication.')
            measured=decode_image(result.image_bytes,ImageLimits()) if isinstance(result,ImageGenerationResult) else None
            if measured is None or (measured['width'],measured['height'],measured['format'])!=(request.width,request.height,request.format) or (result.width,result.height,result.format)!=(request.width,request.height,request.format):raise ValueError('Invalid provider image bytes/dimensions.')
            if self.storyboard()[0].artifact_id!=board.artifact_id or self.image_request(s)[2]!=fingerprint:raise ValueError('Image inputs changed during generation.')
            self.records.store.save_artifact('directed-image.'+request.format.lower(),result.image_bytes,self.records.metadata('image',fingerprint=fingerprint,identity=identity,measurements=measured))
        return len(pending)
    def finish(self):
        m=self.records.latest('finish')
        return self.records.read(m) if m else {'style':ShortStyle(captions=True,burn=True,font_size=32).to_payload(),'branding':False}
    def save_finish(self,value):
        if set(value)!={'style','branding'} or type(value['branding']) is not bool:raise ValueError('Invalid finishing settings.')
        style=ShortStyle.from_payload(value['style'])
        if style.hook and not 2<=style.hook_seconds<=4:raise ValueError('Director hook must be 2-4 seconds.')
        current=self.records.latest('finish')
        return current if current and self.records.read(current)==value else self.records.save('finish',value)
    def captions(self):
        _,script=self.approved_script();m,n,raw,audio=self.narration(approved=True)
        segments=sentence_captions(audio,script['narration'])
        identity={'audio_id':n['audio_id'],'audio_checksum':n['checksum'],'text_checksum':sha256(script['narration'].encode()).hexdigest(),'method':'verified_sentence_assembly','segments':[asdict(s) for s in segments]}
        return segments,identity
    def prepare_render(self):
        self.source();script,_=self.approved_script();n,nvalue,raw,audio=self.narration(approved=True);board,b=self.storyboard()
        images=[self.image(s) for s in b['scenes']];segments,caption=self.captions();finish=self.finish();style=ShortStyle.from_payload(finish['style'])
        overlays(style,segments)
        logo=None;brand=None
        if finish['branding']:
            finishing=self.shorts.publishing.finishing
            if finishing is None or not finishing.branding.current().logo_id:raise ValueError('Configure a D068 channel logo, or disable branding.')
            config=finishing.branding.current();manifest,payload=finishing.branding.logo(config.logo_id)
            logo=payload;brand={'logo_id':manifest.artifact_id,'checksum':manifest.checksum,'channel':config.channel_name,'accent':config.accent}
        if self.renderer is None:raise ValueError('Configure trusted local FFmpeg/FFprobe.')
        identity={'version':'director-v1','source':self.source(),'script_id':script.artifact_id,'narration_id':n.artifact_id,'audio_checksum':audio.checksum,
            'storyboard_id':board.artifact_id,'storyboard':b,'images':[[m.artifact_id,m.checksum,provenance] for m,_,provenance in images],
            'captions':caption,'finish':finish,'branding':brand,'renderer':self.renderer.identity(),'derived_project_id':self.scope.session.project.id}
        return identity,content_fingerprint(identity),(b,images,raw,audio,style,segments,logo)
    async def render(self,*,canceled=lambda:False,progress=lambda *_:None):
        identity,fingerprint,values=self.prepare_render();cached=self.records.cached(fingerprint)
        if canceled():raise RenderCanceled('Render canceled before execution.')
        if cached:return cached
        b,images,raw,audio,style,segments,logo=values
        root=contained_path(self.scope.root,'work/render/'+uuid4().hex);root.mkdir(parents=True)
        (root/'audio.wav').write_bytes(raw)
        for i,(_,payload,_) in enumerate(images):
            with Image.open(BytesIO(payload)) as image:image.convert('RGB').save(root/f'image-{i}.png')
        if logo:(root/'logo.png').write_bytes(logo)
        report=await self.renderer.render(root,b,audio,style,segments,logo=bool(logo),canceled=canceled,progress=progress)
        if canceled():raise RenderCanceled('Render canceled before publication.')
        if self.prepare_render()[0]!=identity:raise ValueError('Director inputs changed during rendering; preceding selected output retained.')
        if (not report.get('fully_decoded') or (report.get('width'),report.get('height'),report.get('sample_rate'))!=(1080,1920,48000)
            or report.get('fps')!=b['fps'] or report.get('frame_count')!=b['scenes'][-1]['end_frame']
            or report.get('video_codec')!='h264' or report.get('audio_codec')!='aac'
            or not 30<=Fraction(*report['audio_duration'])<=60
            or abs(Fraction(*report['audio_duration'])-Fraction(audio.frame_count,audio.sample_rate))>Fraction(1024,48000)):
            raise ValueError('Invalid/partial directed render evidence.')
        path=root/'director.mp4'
        with path.open('rb') as stream:
            if file_digest(stream,'sha256').hexdigest()!=report['checksum'] or path.stat().st_size!=report['size_bytes']:raise ValueError('Encoded bytes changed after decode.')
            stream.seek(0)
            class Checked:
                def __init__(self):self.digest,self.size=sha256(),0
                def read(self,count):
                    if canceled():raise RenderCanceled('Director canceled during publication.')
                    block=stream.read(count);self.digest.update(block);self.size+=len(block)
                    if not block and (self.digest.hexdigest()!=report['checksum'] or self.size!=report['size_bytes']):raise ValueError('Director bytes changed during publication.')
                    return block
            video=self.records.store.import_stream('director.mp4',Checked(),self.records.metadata('video',fingerprint=fingerprint,identity=identity,director=report))
        srt=self.records.store.save_artifact('captions.srt',serialize_srt(segments),self.records.metadata('srt',identity=identity['captions']))
        meta=self.records.save('metadata',{'title':self.approved_script()[1]['title'],'identity':identity,'output_checksum':video.checksum,'video_id':video.artifact_id,
            'duration':report['video_duration'],'resolution':[1080,1920],'video_codec':'h264','audio_codec':'aac','status':'READY; machine checks passed, editorial approval remains human'})
        self.records.save('selection',{'video_id':video.artifact_id,'metadata_id':meta.artifact_id,'srt_id':srt.artifact_id,'fingerprint':fingerprint})
        return video
    async def preview_scene(self,scene_id,*,canceled=lambda:False):
        m,b=self.storyboard();scene=next(s for s in b['scenes'] if s['id']==scene_id)
        image,raw,provenance=self.image(scene);segments,identity=self.captions()
        from app.domain.caption_track import validate_caption_segments
        from dataclasses import replace
        segment=next(s for s in segments if s.text==scene['text'])
        duration_ms=round(Fraction(scene['end_sample']-scene['start_sample'],self.narration(approved=True)[3].sample_rate)*1000)
        shown=validate_caption_segments([{'start_ms':0,'end_ms':duration_ms,'text':segment.text}])
        style=ShortStyle.from_payload(self.finish()['style']);style=replace(style,hook=style.hook if scene['start_frame']==0 else '')
        if self.renderer is None:raise ValueError('Configure trusted FFmpeg/FFprobe.')
        root=contained_path(self.scope.root,'work/preview/'+uuid4().hex);root.mkdir(parents=True)
        with Image.open(BytesIO(raw)) as picture:picture.convert('RGB').save(root/'image-0.png')
        local={'fps':b['fps'],'scenes':[{**scene,'start_frame':0,'end_frame':scene['end_frame']-scene['start_frame']}]}
        raw=await self.renderer.preview(root,local,style,shown,canceled=canceled)
        if self.storyboard()[0].artifact_id!=m.artifact_id:raise ValueError('Storyboard changed during preview.')
        return raw
    def current_output(self):
        _,fingerprint,_=self.prepare_render();m=self.records.cached(fingerprint)
        if m is None:raise ValueError('STALE/BLOCKED: render the current directed Short.')
        return m
    def status(self):
        result={'script':'DRAFT','narration':'BLOCKED','storyboard':'BLOCKED','render':'BLOCKED'}
        try:self.approved_script();result['script']='APPROVED'
        except ValueError:result['script']='REVIEW REQUIRED' if self.script()[0] else 'DRAFT';return result
        try:self.narration(approved=True);result['narration']='APPROVED'
        except ValueError as exc:result['narration']=str(exc);return result
        try:self.storyboard();result['storyboard']='REVIEW REQUIRED'
        except ValueError as exc:result['storyboard']=str(exc);return result
        try:self.current_output();result['render']='READY'
        except ValueError as exc:result['render']=str(exc)
        return result
