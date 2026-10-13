"""Independent editorial script and measured-sentence production contracts."""
from copy import deepcopy
from fractions import Fraction
import re
from app.domain.dependencies import content_fingerprint
from app.domain.caption_track import validate_caption_segments
from app.domain.timeline import OutputTimebase

PARTS = ("hook", "development", "payoff", "cta")

def narration(value): return " ".join(value[p].strip() for p in PARTS if value[p].strip())

def script_schema(refs, language, target):
    text = {"type":"string","minLength":1,"maxLength":2000}
    properties = {k:deepcopy(text) for k in ("title","hook","development","payoff","narration","style")}
    properties.update(language={"type":"string","enum":[language]},target_duration={"type":"integer","enum":[target]},
        cta={"type":"string","maxLength":200},source_refs={"type":"array","minItems":1,"items":{"type":"string","enum":list(refs)}},
        claims_review={"type":"array","items":{"type":"string","maxLength":500}},
        beats={"type":"array","minItems":3,"maxItems":15,"items":{"type":"object","additionalProperties":False,
            "required":["text","visual_concept","source_refs"],"properties":{"text":text,"visual_concept":text,
                "source_refs":{"type":"array","minItems":1,"items":{"type":"string","enum":list(refs)}}}}})
    return {"type":"object","additionalProperties":False,"required":list(properties),"properties":properties}


def validate_script(value, passages, language, target, *, generated=True, cta_enabled=False):
    schema=script_schema(passages,language,target)
    if type(value) is not dict or set(value)!=set(schema["required"]): raise ValueError("Invalid strict Short script structure.")
    for name in ("title","hook","development","payoff","narration","style"):
        if type(value[name]) is not str or not value[name].strip() or len(value[name])>2000: raise ValueError("Missing/oversized script text.")
    if len(value['title'])>100 or value['language']!=language or type(value['target_duration']) is not int or value['target_duration']!=target or not 30<=target<=60:
        raise ValueError("Short title, language or duration does not match the request.")
    if type(value['cta']) is not str or len(value['cta'])>200 or value['cta'] and not cta_enabled:
        raise ValueError("CTA must be explicitly enabled.")
    if value['narration']!=narration(value): raise ValueError("Full narration must exactly match Hook/Development/Payoff/CTA.")
    def refs(items):
        if type(items) is not list or not items or any(type(x) is not str for x in items) or len(set(items))!=len(items) or any(x not in passages for x in items):
            raise ValueError("Invented or invalid source passage reference.")
    refs(value['source_refs'])
    if type(value['claims_review']) is not list or any(type(x) is not str or not x.strip() or len(x)>500 for x in value['claims_review']):
        raise ValueError("Invalid editorial review claims.")
    if type(value['beats']) is not list or not 3<=len(value['beats'])<=15: raise ValueError("Short requires a coherent editorial beat list.")
    for b in value['beats']:
        if type(b) is not dict or set(b)!={'text','visual_concept','source_refs'} or any(type(b[k]) is not str or not b[k].strip() or len(b[k])>2000 for k in ('text','visual_concept')):
            raise ValueError("Invalid editorial beat.")
        refs(b['source_refs'])
        if set(b['source_refs'])-set(value['source_refs']): raise ValueError("Beat references must be declared by the script.")
    if ' '.join(b['text'].strip() for b in value['beats'])!=value['narration']: raise ValueError("Beats must cover the full rewritten narration once, in order.")
    if generated:
        numbers=lambda s:set(re.findall(r"\b\d+(?:[.,]\d+)?%?",s))
        if numbers(value['title']+' '+value['narration'])-numbers(' '.join(passages[r]['text'] for r in value['source_refs'])):
            raise ValueError("Generated script introduced unsupported numeric claims.")
    return value


def measured_sentences(audio, text, fps):
    mapping=audio.speech_boundary_map
    if mapping is None or mapping.quality!='measured_sentence_blocks':
        raise ValueError("BLOCKED: narration requires verified sentence boundaries or explicit measured alignment; no estimated timings.")
    mapping.validate_source(text,audio.checksum,audio.sample_rate,audio.frame_count)
    fps=Fraction(fps); timebase=OutputTimebase(fps.denominator,fps.numerator)
    result=[];occurrences={}
    for b in mapping.blocks:
        first=timebase.frame_at(Fraction(b.start_frame,audio.sample_rate));last=timebase.frame_at(Fraction(b.end_frame,audio.sample_rate))
        if first>=last: raise ValueError("Sentence has no whole video frame.")
        spoken=text[b.source_start:b.source_end].strip();occurrence=occurrences.get(spoken,0);occurrences[spoken]=occurrence+1
        result.append({'id':'director_scene_'+content_fingerprint([audio.section_id,spoken,occurrence])[:24],
            'sentence_id':b.sentence_id,'source_start':b.source_start,'source_end':b.source_end,
            'start_sample':b.start_frame,'end_sample':b.end_frame,'start_frame':first,'end_frame':last,
            'text':text[b.source_start:b.source_end].strip()})
    if result[0]['start_sample']!=0 or result[-1]['end_sample']!=audio.frame_count:
        raise ValueError("Measured storyboard must cover all narration.")
    return result


def sentence_captions(audio, text):
    measured_sentences(audio,text,Fraction(25))
    raw=[{'start_ms':round(Fraction(b.start_frame,audio.sample_rate)*1000),
          'end_ms':round(Fraction(b.end_frame,audio.sample_rate)*1000),'text':text[b.source_start:b.source_end].strip()}
         for b in audio.speech_boundary_map.blocks]
    return validate_caption_segments(raw)
