"""Desktop publishing preparation, composing existing immutable media contracts."""

import asyncio
from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction
from hashlib import file_digest, sha256
from io import BytesIO
import json
import re
import shutil
from uuid import uuid4
import zipfile

from PIL import Image

from app.application.captions import SynchronizedCaptionService
from app.application.speech_alignment import SpeechAlignmentService
from app.domain.caption_track import build_synchronized_caption_track, serialize_srt
from app.domain.dependencies import canonical_json, content_fingerprint
from app.domain.section_audio import SectionAudio
from app.domain.publishing_package import (METADATA_SCHEMA, CompositionTiming, chapter_text,
    timeline_chapters, validate_chapters, validate_metadata)
from app.domain.video_plan import VideoFormat
from app.providers.local_thumbnail import decode_image
from app.runtime.media_process import RenderCanceled
from app.storage.captions import ProjectCaptionTracks
from app.storage.paths import contained_path, import_path
from app.storage.publishing_package import ProjectPublishing


class PublishingPackageService:
    def __init__(self, session, store, renders, timelines, plans, plan_scripts, thumbnail_builder,
                 *, delivery=None, finishing=None, llm=None, alignment=None, quality=None):
        self.session, self.store, self.renders, self.timelines = session, store, renders, timelines
        self.plans, self.plan_scripts, self.thumbnails = plans, plan_scripts, thumbnail_builder
        self.delivery, self.finishing, self.llm, self.alignment = delivery, finishing, llm, alignment
        self.artifacts = ProjectPublishing(store, session.project.id)
        self.captions = ProjectCaptionTracks(session.repository, store)
        self._original_verification = None
        self.quality = quality

    def original(self):
        source = self.renders.selected(verify_bytes=True)
        current = self.timelines.current() if self.timelines else None
        if source is None or current is None: raise ValueError("MISSING: select a verified final render and Timeline first.")
        timeline = current.timeline
        settings = source.metadata.get("desktop_dependencies", {}).get("request", {}).get("settings", {})
        if settings.get("timeline") != timeline.to_payload() or not self.renders.motion_current(source):
            raise ValueError("STALE: the final render does not match the current Timeline/motion settings.")
        revisions = {s.section_id: s.id for s in self.session.active_script.sections}
        if any(revisions.get(c.media.section_id) != c.media.section_revision_id for c in timeline.clips):
            raise ValueError("STALE: final video references an earlier saved script.")
        evidence = source.metadata.get("render", {})
        if not evidence.get("fully_decoded") or evidence.get("timeline_id") != timeline.id:
            raise ValueError("BLOCKED: final video lacks verified Timeline/decode evidence.")
        # Reuse an expensive media-binding check while the immutable upstream
        # catalog, selections and script remain unchanged. MP4 bytes are always
        # reverified above; publishing edits never regenerate/recompile media.
        catalog = [[m.artifact_id, m.checksum] for m in self.store.list_artifacts()
                   if not m.artifact_type.startswith(("publishing_", "shorts_", "quality_")) and m.artifact_type not in {
                       "desktop_speech_alignment", "desktop_caption_track", "desktop_caption_srt", "desktop_caption_ass"}]
        heads = self.renders.index.selected() if hasattr(self.renders, "index") else {}
        key = content_fingerprint([source.artifact_id, source.checksum, timeline.id, self.session.active_script.id, catalog, heads])
        if self._original_verification != key:
            self.renders.current(timeline)
            self._original_verification = key
        return source, timeline

    def source(self, kind):
        original, timeline = self.original()
        if kind == "original": source, evidence = original, original.metadata["render"]
        elif kind == "branded":
            if not self.delivery: raise ValueError("MISSING: current branded render unavailable.")
            source = self.delivery.source("branded"); evidence = source.metadata["finishing"]
        elif kind == "delivery":
            if not self.delivery: raise ValueError("MISSING: verified delivery MP4 unavailable.")
            config = self.delivery.artifacts.current()
            base, _, fingerprint = self.delivery.prepare(config)
            source = self.delivery.artifacts.cached(fingerprint)
            if source is None: raise ValueError("MISSING: build the current YouTube delivery MP4 first.")
            evidence = source.metadata["delivery"]
            if evidence.get("source_artifact_id") != base.artifact_id or evidence.get("source_checksum") != base.checksum:
                raise ValueError("BLOCKED: delivery source identity mismatch.")
        else: raise ValueError("Choose an eligible original, branded or delivery video.")
        if (source.metadata.get("project_id") != self.session.project.id or not evidence.get("fully_decoded")
                or evidence.get("checksum") != source.checksum or evidence.get("size_bytes") != source.size_bytes):
            raise ValueError("BLOCKED: video verification evidence does not match its identity.")
        return source, evidence, timeline

    def sources(self):
        values = []
        for kind in ("original", "branded", "delivery"):
            try: values.append((kind, *self.source(kind)[:2]))
            except (ValueError, OSError, KeyError): continue
        return values

    def timing(self, kind):
        source, evidence, timeline = self.source(kind)
        original, _ = self.original()
        underlying = source
        if kind == "delivery":
            underlying = self.artifacts.one(evidence["source_artifact_id"])
            source_evidence = underlying.metadata["render" if underlying.artifact_type == "video_render" else "finishing"]
            if (Fraction(*evidence["video_duration"]) != Fraction(*source_evidence["video_duration"])
                    or evidence["frame_count"] != source_evidence["frame_count"]
                    or Fraction(str(evidence["fps"])) != Fraction(str(source_evidence["fps"]))):
                raise ValueError("BLOCKED: delivery timing is not compatible with its source.")
        intro = outro = insertion = Fraction(0)
        if underlying.artifact_type == "branded_video":
            identity = underlying.metadata.get("identity", {})
            settings = identity.get("settings", {})
            if identity.get("source_id") != original.artifact_id or identity.get("source_checksum") != original.checksum:
                raise ValueError("BLOCKED: missing verified branding composition source.")
            from app.domain.channel_library import branding_config, ReusableBrandingConfig
            config = branding_config(settings)
            intro = Fraction(config.intro_seconds if config.intro else 0)
            outro = Fraction(config.outro_seconds if config.outro else 0)
            if isinstance(config,ReusableBrandingConfig):
                from app.domain.branding_timing import verified_timing_map
                mapped=verified_timing_map(underlying,original,timeline)
                intro=Fraction(*mapped["intro"]["duration"]) if mapped["intro"] else Fraction(0)
                outro=Fraction(*mapped["outro"]["duration"]) if mapped["outro"] else Fraction(0)
            if intro and config.placement == "after_cold_open":
                boundary = identity.get("boundary")
                if not boundary or boundary.get("timeline_id") != timeline.id:
                    raise ValueError("BLOCKED: missing verified cold-open composition boundary.")
                insertion = Fraction(boundary["audio_sample"], original.metadata["render"]["sample_rate"])
                video_boundary = Fraction(boundary["video_frame"], Fraction(str(original.metadata["render"]["fps"])))
                ends = {c.audio_offset + c.duration for c in timeline.clips}
                if insertion not in ends or abs(insertion - video_boundary) > timeline.timebase.seconds:
                    raise ValueError("BLOCKED: branding boundary does not match an exact Timeline boundary.")
            expected_frames = original.metadata["render"]["frame_count"] + round((intro + outro) * Fraction(str(evidence["fps"])))
            if evidence["frame_count"] != expected_frames:
                raise ValueError("BLOCKED: branded frame count is incompatible with composition timing.")
        elif underlying.artifact_id != original.artifact_id:
            raise ValueError("BLOCKED: video lineage is not the selected original.")
        return CompositionTiming(timeline.duration, Fraction(*evidence["video_duration"]), intro=intro, insertion=insertion, outro=outro)

    def context(self, kind, links=()):
        source, evidence, timeline = self.source(kind)
        plan = self.plans.selected()
        script = self.session.active_script
        if any(not re.fullmatch(r"https?://[^\s<>]+", link) for link in links):
            raise ValueError("Configured channel links must be explicit HTTP(S) URLs.")
        channel = self.finishing.branding.current().channel_name if self.finishing else ""
        included_ids = list(dict.fromkeys(c.media.section_id for c in timeline.clips))
        included = [script.section(section_id) for section_id in included_ids]
        context = {"project_id": self.session.project.id, "source_artifact_id": source.artifact_id,
            "source_checksum": source.checksum, "timeline_id": timeline.id, "script_revision_id": script.id,
            "working_title": plan.working_title if plan else self.session.project.name,
            "topic": plan.topic if plan else self.session.project.name, "film_brief": plan.film_brief if plan else "",
            "format": plan.format.value if plan else "unknown", "video_plan": plan.to_payload() if plan else None,
            "saved_script": [asdict(s) for s in script.sections],
            "included_sections": [{"section_id": s.section_id, "title": s.title} for s in included],
            "selected_timeline_clips": [{"section_id": c.media.section_id, "revision_id": c.media.section_revision_id,
                "audio_artifact_id": c.media.audio.artifact_id, "source_samples": [c.media.audio.start_sample, c.media.audio.end_sample],
                "source_frame_count": c.media.audio.frame_count, "audio_offset": [c.audio_offset.numerator, c.audio_offset.denominator],
                "duration": [c.duration.numerator, c.duration.denominator]} for c in timeline.clips],
            "duration_seconds": float(Fraction(*evidence["video_duration"])),
            "language": self.session.project.language, "channel_name": channel, "configured_links": list(links)}
        return context, content_fingerprint(context)

    async def generate_metadata(self, kind, links=()):
        context, fingerprint = self.context(kind, links)
        if self.llm is None:
            title = context["working_title"].strip()[:82]
            facts = context["working_title"][:100] + "\n\nTopics covered:\n" + "\n".join(s["title"] for s in context["included_sections"])
            metadata = {"titles": {"curiosity": f"Exploring {title}"[:100], "search": title[:100], "cinematic": f"Inside {title}"[:100]},
                "description": facts.encode("utf-8")[:4000].decode("utf-8", errors="ignore"),
                "keywords": [context["working_title"][:80]], "hashtags": [], "pinned_comment": ""}
            validate_metadata(metadata, allowed_urls=links)
            origin, provider = "deterministic starting draft (not AI)", None
        else:
            prompt = ("Create three differentiated YouTube titles: curiosity, search/discovery, cinematic/documentary. "
                "Each <=100 characters, accurate and restrained. No excessive capitals, fabricated numbers, claims, links, citations or sponsors. "
                "Write an accurate description hook/summary/topics and optional CTA/channel copy using only known facts, "
                "under 4,000 UTF-8 bytes to leave room for verified chapters. Tags must fit 500 characters combined, "
                "counting separators and quotes around multiword tags. No angle brackets in title/description. "
                "The Timeline's included_sections/selected_timeline_clips define actual coverage; do not promise planned runtimes "
                "or removed script topics. Partial audio spans require editorial verification of the retained wording. "
                "Do not generate chapter timestamps or a chapters block: those are inserted from verified Timeline timing. "
                "Suggest relevant keywords, exactly three hashtags and an optional pinned comment in the project language. "
                "Only explicitly configured links may be included. Editorial review is always required. Context:\n" + canonical_json(context))
            metadata = await asyncio.to_thread(self.llm.generate_structured, prompt, deepcopy(METADATA_SCHEMA))
            validate_metadata(metadata, allowed_urls=links, generated_context=canonical_json(context))
            origin = "LLM candidate — editorial review required"
            provider = self.llm.generation_identity() if hasattr(self.llm, "generation_identity") else {"provider": self.llm.provider_name}
        return self.artifacts.save("candidates", {"version": 1, "context": context, "context_fingerprint": fingerprint,
            "metadata": metadata, "origin": origin, "provider": provider})

    def starting_draft(self, kind):
        context, fingerprint = self.context(kind)
        return {"version": 1, "source_kind": kind, "context_fingerprint": fingerprint, "metadata": None,
                "selected_title": "search", "candidate_id": None, "approved": False, "links": [],
                "chapters_enabled": context["format"] != "social", "chapters": [], "subtitle_id": None,
                "thumbnail_id": None, "parent_revision_id": None}

    def save_draft(self, value, *, expected_revision_id):
        latest, _ = self.artifacts.latest()
        if (latest.artifact_id if latest else None) != expected_revision_id:
            raise ValueError("Publishing draft changed; reload before saving.")
        required = set(self.starting_draft(value["source_kind"]))
        if set(value) != required or value["version"] != 1 or type(value["approved"]) is not bool or type(value["chapters_enabled"]) is not bool:
            raise ValueError("Invalid publishing draft structure.")
        context, fingerprint = self.context(value["source_kind"], value["links"])
        validate_metadata(value["metadata"], allowed_urls=value["links"])
        if value["selected_title"] not in value["metadata"]["titles"]: raise ValueError("Choose a suggested or manually edited title.")
        if value["candidate_id"]: self.artifacts.one(value["candidate_id"], "candidates")
        if value["subtitle_id"]: self.artifacts.one(value["subtitle_id"], "subtitles")
        if value["thumbnail_id"]: self.artifacts.one(value["thumbnail_id"], "thumbnail")
        if value["context_fingerprint"] != fingerprint:
            raise ValueError("STALE: review the changed source/context before saving this draft for it.")
        result = {**value, "parent_revision_id": expected_revision_id}
        return self.artifacts.save("draft", result, context=context, approved=value["approved"])

    def chapters(self, kind):
        _, _, timeline = self.source(kind)
        plan = self.plans.selected()
        if plan and plan.format == VideoFormat.SOCIAL: return []
        return timeline_chapters(plan, self.plan_scripts.active_binding(), self.session.active_script, timeline, self.timing(kind))

    def _aligned(self, timeline):
        active = self.session.active_script
        result, missing = [], []
        for section_id in dict.fromkeys(c.media.section_id for c in timeline.clips):
            clips = [c for c in timeline.clips if c.media.section_id == section_id]
            section = active.section(section_id)
            span = clips[0].media.audio
            if any(c.media.audio.artifact_id != span.artifact_id for c in clips):
                raise ValueError("BLOCKED: section uses multiple audio identities; alignment selection is ambiguous.")
            audio = SectionAudio.from_manifest(self.artifacts.one(span.artifact_id))
            self.captions.alignments.inputs(section, audio)
            alignment = self.captions.alignments.latest(section_id, revision_id=section.id, audio_artifact_id=audio.artifact_id)
            if alignment is None or alignment.outcome != "complete" or alignment.audio_checksum != audio.checksum:
                missing.append((section, audio))
            else: result.append(alignment)
        return result, missing

    async def generate_missing_alignment(self, kind, *, canceled=lambda: False):
        if self.alignment is None: raise ValueError("MISSING: configure an alignment provider or import valid measured alignment artifacts.")
        _, _, timeline = self.source(kind)
        _, missing = self._aligned(timeline)
        for section, audio in missing:
            if canceled(): raise RenderCanceled("Alignment preparation canceled; completed alignments retained.")
            raw, _, _ = self.captions.alignments.inputs(section, audio)
            provider = self.alignment.provider
            result = await asyncio.to_thread(provider.align, raw, section.text, self.session.project.language)
            if canceled(): raise RenderCanceled("Alignment preparation canceled before publication.")
            class Replay:
                provider_name, model_name = provider.provider_name, provider.model_name
                def align(self, *_): return result
                def effective_alignment_identity(self): return provider.effective_alignment_identity()
            SpeechAlignmentService(self.captions.alignments, Replay(), confidence_threshold=self.alignment.confidence_threshold).align(
                section, audio, language=self.session.project.language)
        return len(missing)

    def subtitles(self, kind, *, create=True):
        source, _, timeline = self.source(kind)
        timing = self.timing(kind)
        alignments, missing = self._aligned(timeline)
        if missing: raise ValueError(f"MISSING: measured alignment for {len(missing)} section(s). Use Generate missing alignment with a configured provider.")
        desired = build_synchronized_caption_track(timeline,
            {a.section_id: self.session.active_script.section(a.section_id).text for a in alignments},
            {a.section_id: a for a in alignments}, language=self.session.project.language)
        published = next((p for p in self.captions.history(timeline.id) if p.track == desired), None)
        if published is None:
            if not create: raise ValueError("STALE: synchronized caption track must be regenerated explicitly.")
            published = SynchronizedCaptionService(self.captions).export(timeline, [a.id for a in alignments], language=self.session.project.language)
        segments = timing.captions(published.track.segments)
        identity = {"source_id": source.artifact_id, "source_checksum": source.checksum,
            "track": published.to_payload(), "timing": timing.to_payload(), "algorithm": "publishing-srt-v1"}
        fingerprint = content_fingerprint(identity)
        for m in reversed(self.artifacts.history("subtitles")):
            if m.metadata.get("fingerprint") == fingerprint:
                if self.artifacts.bytes(m) == serialize_srt(segments).encode("utf-8"): return m
        if not create: raise ValueError("STALE: timed subtitle export must be regenerated explicitly.")
        return self.store.save_artifact(f"subtitles.{self.session.project.language}.srt", serialize_srt(segments).encode("utf-8"),
            {"artifact_type": "publishing_subtitles", "module_name": "desktop_publishing", "project_id": self.session.project.id,
             "fingerprint": fingerprint, "identity": identity, "language": self.session.project.language})

    def import_image(self, path):
        path = import_path(path)
        if path.stat().st_size > 32 * 1024 * 1024: raise ValueError("Thumbnail source exceeds 32 MiB.")
        image = decode_image(path.read_bytes())
        stream = BytesIO(); image.save(stream, format="PNG")
        return self.store.save_artifact("thumbnail-source.png", stream.getvalue(),
            {"artifact_type": "publishing_image", "module_name": "desktop_publishing", "project_id": self.session.project.id, "origin": "imported"})

    def scene_images(self, kind):
        _, _, timeline = self.source(kind)
        result = []
        for clip in timeline.clips:
            manifest = self.artifacts.one(clip.media.image.artifact_id)
            if manifest.checksum != clip.media.image.checksum: raise ValueError("Current scene image checksum mismatch.")
            decode_image(self.artifacts.bytes(manifest)); result.append(manifest)
        return list({m.artifact_id: m for m in result}.values())

    async def frame_suggestions(self, kind, *, canceled=lambda: False):
        source, evidence, _ = self.source(kind)
        frames = [int((evidence["frame_count"] - 1) * n / 10) for n in (2, 5, 8)]
        if len(set(frames)) != 3: raise ValueError("Video is too short for three distinct suggestions.")
        root = contained_path(self.session.repository.workspace, "work/publishing/" + uuid4().hex); root.mkdir(parents=True)
        with self.store.open_artifact_id(source.artifact_id) as stream, (root / "source.mp4").open("xb") as target:
            shutil.copyfileobj(stream, target, 1024 * 1024)
        from app.providers.ffmpeg_render import checksum
        if checksum(root / "source.mp4") != source.checksum: raise ValueError("Video changed while extracting thumbnails.")
        raw_frames = await self.thumbnails.frames(root, frames, fps=evidence["fps"], canceled=canceled)
        if len(raw_frames) != 3: raise ValueError("Frame extraction must return all three requested suggestions.")
        if self.source(kind)[0] != source: raise ValueError("Selected source changed during frame extraction.")
        if any(decode_image(raw).size != (evidence["width"], evidence["height"]) for raw in raw_frames):
            raise ValueError("Extracted frame dimensions differ from the selected verified video.")
        if canceled(): raise RenderCanceled("Frame preparation canceled before publication.")
        return [self.store.save_artifact(f"frame-{index}.png", raw,
            {"artifact_type": "publishing_image", "module_name": "desktop_publishing", "project_id": self.session.project.id,
             "origin": "video frame", "source_id": source.artifact_id, "source_checksum": source.checksum, "frame_index": index})
            for index, raw in zip(frames, raw_frames)]

    def thumbnail_candidates(self, image_id, settings):
        image = self.artifacts.one(image_id)
        if image.artifact_type not in {"publishing_image", "desktop_scene_image"}:
            # Current scene image contracts use scene_image; eligibility is checked below.
            if image_id not in {m.artifact_id for m in self.scene_images(settings["source_kind"])}:
                raise ValueError("Choose an imported image, verified video frame or current scene image.")
        if image.metadata.get("source_id"):
            source = self.source(settings["source_kind"])[0]
            if (image.metadata["source_id"], image.metadata.get("source_checksum")) != (source.artifact_id, source.checksum):
                raise ValueError("STALE: thumbnail frame is from another video source.")
        options = {k: v for k, v in settings.items() if k != "source_kind"}
        logo_id = options.pop("logo_id", None)
        logo = self.finishing.branding.logo(logo_id)[1] if logo_id and self.finishing else None
        if logo_id and logo is None: raise ValueError("Current verified channel logo is unavailable.")
        identity = {"image_id": image.artifact_id, "checksum": image.checksum, "settings": settings,
                    "builder": self.thumbnails.identity(), "logo_checksum": sha256(logo).hexdigest() if logo else None}
        fingerprint = content_fingerprint(identity)
        cached = {}
        for m in reversed(self.artifacts.history("thumbnail")):
            if m.metadata.get("fingerprint") == fingerprint and m.metadata.get("style") not in cached:
                try: self.verify_thumbnail(m)
                except (ValueError, OSError): continue
                cached[m.metadata["style"]] = m
        if set(cached) == {"bold", "cinematic", "documentary"}:
            return [cached[style] for style in ("bold", "cinematic", "documentary")]
        values = self.thumbnails.layouts(self.artifacts.bytes(image), logo=logo, **options)
        return [self.store.save_artifact(f"thumbnail-{style}.jpg", raw,
            {"artifact_type": "publishing_thumbnail", "module_name": "desktop_publishing", "project_id": self.session.project.id,
             "fingerprint": fingerprint, "identity": identity, "style": style, "width": size[0], "height": size[1]}) for style, raw, size in values]

    def verify_thumbnail(self, manifest):
        manifest = self.artifacts.one(manifest.artifact_id, "thumbnail")
        raw = self.artifacts.bytes(manifest)
        with Image.open(BytesIO(raw)) as image:
            image.load()
            if image.format != "JPEG" or image.mode != "RGB" or image.width * 9 != image.height * 16 or image.size != (manifest.metadata["width"], manifest.metadata["height"]):
                raise ValueError("Invalid thumbnail dimensions/color/format.")
        return raw

    def validate(self, draft_manifest):
        draft = self.artifacts.read(self.artifacts.one(draft_manifest.artifact_id, "draft"))
        context, fingerprint = self.context(draft["source_kind"], draft["links"])
        if draft["context_fingerprint"] != fingerprint: raise ValueError("STALE: saved publishing context differs from the selected video/script/plan.")
        if not draft["approved"]: raise ValueError("REVIEW REQUIRED: review metadata accuracy and explicitly approve a saved revision.")
        validate_metadata(draft["metadata"], allowed_urls=draft["links"])
        source, evidence, timeline = self.source(draft["source_kind"])
        duration = Fraction(*evidence["video_duration"])
        if context["format"] == "standard" and not draft["chapters_enabled"]:
            raise ValueError("CHAPTERS: REVIEW REQUIRED — Standard packages require valid chapters; correct ambiguous mappings manually.")
        if draft["chapters_enabled"]: validate_chapters(draft["chapters"], duration)
        if not draft["thumbnail_id"]: raise ValueError("MISSING: select a validated thumbnail and save the revision.")
        thumb = self.artifacts.one(draft["thumbnail_id"], "thumbnail"); self.verify_thumbnail(thumb)
        image = self.artifacts.one(thumb.metadata["identity"]["image_id"])
        if image.metadata.get("source_id") and (image.metadata["source_id"], image.metadata.get("source_checksum")) != (source.artifact_id, source.checksum):
            raise ValueError("STALE: selected thumbnail frame belongs to another video; regenerate or explicitly import a custom image.")
        if image.artifact_type == "scene_image" and image.artifact_id not in {c.media.image.artifact_id for c in timeline.clips}:
            raise ValueError("STALE: thumbnail uses a scene image no longer selected in the Timeline.")
        subtitle = self.artifacts.one(draft["subtitle_id"], "subtitles") if draft["subtitle_id"] else None
        if subtitle and self.subtitles(draft["source_kind"], create=False).artifact_id != subtitle.artifact_id:
            raise ValueError("STALE: selected captions no longer match current alignment/video timing.")
        identity = {"algorithm": "publishing-package-v1", "context": context,
            "draft_id": draft_manifest.artifact_id, "draft_checksum": draft_manifest.checksum,
            "source_id": source.artifact_id, "source_checksum": source.checksum, "timeline_id": timeline.id,
            "chapters": draft["chapters"] if draft["chapters_enabled"] else [],
            "thumbnail_id": thumb.artifact_id, "thumbnail_checksum": thumb.checksum,
            "subtitle_id": subtitle.artifact_id if subtitle else None, "subtitle_checksum": subtitle.checksum if subtitle else None}
        if self.quality:
            identity["qa"] = self.quality.publishing(source.artifact_id, source.checksum)
        return draft, source, evidence, thumb, subtitle, identity

    def build_package(self, draft_manifest, *, canceled=lambda: False):
        draft, source, evidence, thumb, subtitle, identity = self.validate(draft_manifest)
        fingerprint = content_fingerprint(identity)
        cached = self.artifacts.cached(fingerprint)
        if canceled(): raise RenderCanceled("Publishing package canceled.")
        if cached: return cached
        language = self.session.project.language
        if not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language): raise ValueError("Unsafe subtitle language filename.")
        chapters = identity["chapters"]
        metadata = {"package_schema_version": 1, "project_id": self.session.project.id,
            "selected_video": {"artifact_id": source.artifact_id, "checksum": source.checksum},
            "selected_title": draft["metadata"]["titles"][draft["selected_title"]], "title_alternatives": draft["metadata"]["titles"],
            "description": draft["metadata"]["description"], "chapters": chapters, "keywords": draft["metadata"]["keywords"],
            "hashtags": draft["metadata"]["hashtags"], "pinned_comment": draft["metadata"]["pinned_comment"], "language": language,
            "thumbnail_checksum": thumb.checksum, "subtitle_checksum": subtitle.checksum if subtitle else None,
            "video_duration": evidence["video_duration"], "duration_seconds": float(Fraction(*evidence["video_duration"])),
            "revision_id": draft_manifest.artifact_id, "candidate_id": draft["candidate_id"], "validation_status": "READY — machine checks passed; editorial review approved",
            "source_status": evidence.get("status", "verified original/branded"), "created_at": draft_manifest.created_at.isoformat(), "dependencies": identity}
        metadata["qa"] = identity.get("qa", {"state": "NOT_RUN", "approved": False, "report_id": None})
        missing = []
        description = metadata["description"]
        if chapters: description += "\n\nChapters\n" + chapter_text(chapters)
        else: missing.append("Chapters omitted by explicit preference / not applicable.")
        if not subtitle: missing.append("Subtitles unavailable or deliberately omitted; no estimated timestamps were generated.")
        if not metadata["pinned_comment"]: missing.append("Optional pinned comment omitted.")
        if not metadata["keywords"]: missing.append("Optional tags omitted.")
        hashtags = [s for s in metadata["hashtags"] if s not in description]
        if hashtags: description += "\n\n" + " ".join(hashtags)
        validate_metadata({**draft["metadata"], "description": description}, allowed_urls=draft["links"])
        metadata["description"] = description; metadata["omitted_components"] = missing
        payloads = {"thumbnail.jpg": self.verify_thumbnail(thumb), "description.txt": description.encode("utf-8"),
            "metadata.json": canonical_json(metadata).encode("utf-8"),
            "README.txt": ("Manual YouTube publishing package. No upload performed.\nUse selected_title from metadata.json.\n"
                "Review factual accuracy, thumbnail relevance, accessibility and source licensing before publishing.\n"
                + "Production QA: " + metadata["qa"]["state"] + "; QA approved: " + str(metadata["qa"]["approved"]) + ".\n"
                + "\n".join(missing) + "\n").encode("utf-8")}
        if metadata["keywords"]: payloads["tags.txt"] = ", ".join(metadata["keywords"]).encode("utf-8")
        if chapters: payloads["chapters.txt"] = chapter_text(chapters).encode("utf-8")
        if subtitle: payloads[f"subtitles.{language}.srt"] = self.artifacts.bytes(subtitle)
        if metadata["pinned_comment"]: payloads["pinned-comment.txt"] = metadata["pinned_comment"].encode("utf-8")
        root = contained_path(self.session.repository.workspace, "work/publishing/" + uuid4().hex); root.mkdir(parents=True)
        path = root / "publishing-package.zip"
        files = {}
        with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for name in ["video.mp4", *sorted(payloads)]:
                key = "publishing-package/" + name
                entry = zipfile.ZipInfo(key, date_time=(1980, 1, 1, 0, 0, 0)); entry.external_attr = 0o100644 << 16
                digest, size = sha256(), 0
                stream = self.store.open_artifact_id(source.artifact_id) if name == "video.mp4" else BytesIO(payloads[name])
                with stream, archive.open(entry, "w", force_zip64=True) as target:
                    while block := stream.read(1024 * 1024):
                        if canceled(): raise RenderCanceled("Publishing package canceled; earlier artifacts retained.")
                        digest.update(block); size += len(block); target.write(block)
                files[key] = {"checksum": digest.hexdigest(), "size_bytes": size}
                if name == "video.mp4" and (digest.hexdigest() != source.checksum or size != source.size_bytes):
                    raise ValueError("Video changed while building publishing ZIP.")
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != set(files): raise ValueError("Corrupt publishing ZIP.")
        if self.validate(draft_manifest)[-1] != identity or self.artifacts.latest()[0] != draft_manifest:
            raise ValueError("Publishing inputs changed before package publication.")
        if canceled(): raise RenderCanceled("Publishing package canceled before publication.")
        with path.open("rb") as stream:
            expected_checksum, expected_size = file_digest(stream, "sha256").hexdigest(), path.stat().st_size
            stream.seek(0)
            class CheckedZIP:
                def __init__(self): self.digest, self.size = sha256(), 0
                def read(self, count):
                    if canceled(): raise RenderCanceled("Publishing package canceled during publication.")
                    block = stream.read(count); self.digest.update(block); self.size += len(block)
                    if not block and (self.digest.hexdigest() != expected_checksum or self.size != expected_size):
                        raise ValueError("Publishing ZIP changed during atomic publication.")
                    return block
            return self.store.import_stream("publishing-package.zip", CheckedZIP(),
                {"artifact_type": "publishing_package", "module_name": "desktop_publishing", "project_id": self.session.project.id,
                 "fingerprint": fingerprint, "identity": identity, "files": files, "status": "READY"})
