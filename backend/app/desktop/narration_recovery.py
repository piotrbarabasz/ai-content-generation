"""Read-only narration eligibility and evidence verification. No receipts are inferred."""
import base64
from hashlib import sha256
import json

from app.domain.dependencies import canonical_json, content_fingerprint
from app.domain.publication import PublicationSnapshot
from app.providers.openai_tts import normalize_speech_wav
from app.runtime.section_synthesis import workspace
from app.storage.paths import contained_path
from app.storage.narration import attempt_evidence
from app.tts.assembly import inspect_pcm_wav
from app.tts.chunk_synthesis import ResumableChunkSynthesizer
from app.tts.manifest import SynthesisManifest, sanitize_synthesis_identity, stable_hash

VALID_CACHED = 'VALID_CACHED'
RECOVERABLE_RESPONSE = 'RECOVERABLE_RESPONSE'
ELIGIBLE_FOR_GENERATION = 'ELIGIBLE_FOR_GENERATION'
ATTEMPTS_EXHAUSTED = 'ATTEMPTS_EXHAUSTED'
BLOCKED_CONFIGURATION = 'BLOCKED_CONFIGURATION'



def response_evidence(root, key, provider, *, text, identity):
    """Read complete/partial archives and committed or interrupted cache writes."""
    folder = contained_path(root, 'responses')
    candidates = sorted(folder.glob(key + '*')) if folder.exists() else ()
    evidence, recovered = [], None
    for candidate in candidates:
        if candidate.suffix not in ('.json', '.pending'):
            continue
        entry = {'file': candidate.name, 'valid': False, 'category': 'INVALID_RESPONSE_RECORD'}
        try:
            path = contained_path(root, 'responses/' + candidate.name)
            value = json.loads(path.read_text(encoding='utf-8'))
            payload = base64.b64decode(value['audio'], validate=True)
            entry['bytes'] = len(payload)
            entry['checksum'] = sha256(payload).hexdigest()
            if value['version'] not in (1, 2) or value['key'] != key or entry['checksum'] != value['checksum']:
                raise ValueError('Response identity/checksum mismatch')
            if value['version'] == 2 and (value['text_hash'] != sha256(text.encode()).hexdigest()
                    or value['effective_identity'] != identity):
                raise ValueError('Response source identity mismatch')
            entry['reservation_id'] = value.get('reservation_id')
            entry['provenance'] = 'exact text and synthesis identity; project content-addressed response'
            if value.get('response_state', 'complete') != 'complete':
                entry['category'] = 'INTERRUPTED_DOWNLOAD'
            else:
                normalized = normalize_speech_wav(payload, provider.ffmpeg)
                entry.update(valid=True, category='VERIFIED_RECEIVED_AUDIO')
                recovered = recovered or normalized
        except (ValueError, KeyError, TypeError, OSError) as exc:
            entry['category'] = getattr(exc, 'category', 'INVALID_RESPONSE_RECORD')
        evidence.append(entry)
    return recovered, evidence


def technical_evidence(root, jobs, section, prepared, chunk, provider):
    """Only exact frozen section requests can authorize a technical WAV's provenance."""
    evidence, recovered = [], None
    identity = prepared['effective_identity']['synthesis']
    config_hash = stable_hash({'provider': 'openai', 'effective_synthesis_identity': identity})
    for job in jobs:
        if job.output_key != f'section:{section.section_id}:audio:raw':
            continue
        try:
            if (job.request.settings_json != canonical_json(prepared)
                    or PublicationSnapshot.from_job(job).sections != (section,)):
                continue
            directory = workspace(root / 'jobs', job)
            marker = contained_path(directory, 'request.json')
            if not marker.exists():
                continue
            entry = {'job_id': job.id, 'valid': False, 'category': 'TECHNICAL_REQUEST_MISMATCH'}
            evidence.append(entry)
            if marker.read_text(encoding='utf-8') != canonical_json(job.to_payload()):
                continue
            manifest = SynthesisManifest.from_payload(json.loads(contained_path(directory, 'synthesis-manifest.json').read_text()))
            if (manifest.config_hash != config_hash
                    or manifest.effective_synthesis_identity != sanitize_synthesis_identity(identity)):
                continue
            record = manifest.chunks.get(chunk.id)
            if record is None:
                continue
            entry['technical_invocations'] = record.attempts
            expected = (sha256(chunk.text.encode()).hexdigest(), config_hash, chunk.text_hash, chunk.index, chunk.source_span)
            if (record.input_hash, record.config_hash, record.text_hash, record.index, record.source_span) != expected:
                continue
            payload = ResumableChunkSynthesizer(provider)._reuse_if_valid(record, directory)
            if payload is None:
                entry.update(category='INVALID_OR_MISSING_TECHNICAL_WAV', error=record.error)
                continue
            pcm, _ = inspect_pcm_wav(payload)
            if (pcm.sample_rate, pcm.channels, pcm.sample_width) != (24000, 1, 2):
                continue
            normalize_speech_wav(payload, provider.ffmpeg)  # also check clipping
            entry.update(valid=True, category='VERIFIED_TECHNICAL_CHUNK', checksum=sha256(payload).hexdigest())
            recovered = recovered or payload
        except (ValueError, KeyError, TypeError, OSError):
            continue
    return recovered, evidence


def report_summary(report):
    lines = [f"Narration recovery: {report['reuse']} retained successful sections; {report['regenerate']} unfinished sections."]
    for row in report['chunk_statuses']:
        if row['state'] in (ATTEMPTS_EXHAUSTED, BLOCKED_CONFIGURATION):
            lines.extend([f"{row['section_title']} / {row['chunk_id']} [{row['state']}]",
                          f"Category: {row['category']}. {row['reason']}",
                          f"Reservations: {len(row['attempts'])}; billing UNKNOWN. Another API call required: {row['api_required']}."])
    lines.append('Recovery: Audio > Scan narration recovery. Verify retained responses first; review one exceptional attempt only if unavailable. No automatic restart.')
    return '\n'.join(lines)


def seal_report(report):
    report['blocked'] = [r for r in report['chunk_statuses'] if r['state'] in (ATTEMPTS_EXHAUSTED, BLOCKED_CONFIGURATION)]
    report['evidence_fingerprint'] = content_fingerprint(report)
    report['summary'] = report_summary(report)
    return report
