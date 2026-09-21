"""Verified, regenerable project-local preview cache."""

from hashlib import file_digest
import json
import os
from pathlib import Path
import shutil
import tempfile
import wave

from app.application.preview import ScenePreview
from app.domain.dependencies import content_fingerprint
from app.domain.render_result import RenderedVideo
from app.storage.paths import contained_path


def _checksum(path):
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def _write_json(path, value):
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())


class ProjectPreviewMedia:
    def __init__(self, render_media):
        self.render_media = render_media
        self.root = contained_path(render_media.index.repository.workspace, "cache/previews")
        self.root.mkdir(parents=True, exist_ok=True)

    def current(self, timeline):
        self.render_media.current(timeline)

    def stage(self, timeline):
        return self.render_media.stage(timeline)

    def _path(self, key, suffix):
        return contained_path(self.root, f"{key}.{suffix}")

    def _manifest(self, key):
        path = self._path(key, "json")
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("version") != 1 or value.get("cache_key") != key:
                return None
            return value
        except (OSError, ValueError, TypeError):
            return None

    def cached_proxy(self, key, timeline):
        manifest, path = self._manifest(key), self._path(key, "mp4")
        if (manifest is None or manifest.get("kind") != "film_proxy"
                or manifest.get("timeline_id") != timeline.id or not path.is_file()):
            return None
        try:
            if path.stat().st_size != manifest["size_bytes"] or _checksum(path) != manifest["checksum"]:
                return None
        except (OSError, KeyError, TypeError):
            return None
        return path

    def publish_proxy(self, key, timeline, render_root, result, renderer_identity):
        if not isinstance(result, RenderedVideo) or result.timeline_id != timeline.id:
            raise ValueError("Proxy render evidence belongs to another timeline.")
        source = contained_path(Path(render_root), "render.mp4")
        if (not source.is_file() or source.stat().st_size != result.size_bytes
                or _checksum(source) != result.checksum):
            raise ValueError("Proxy bytes differ from validated render evidence.")
        stage = Path(tempfile.mkdtemp(prefix="proxy-", dir=self.root))
        try:
            payload = {"version": 1, "kind": "film_proxy", "cache_key": key,
                       "timeline_id": timeline.id, "checksum": result.checksum,
                       "size_bytes": result.size_bytes, "renderer": renderer_identity}
            temporary_media = stage / "proxy.mp4"
            shutil.copyfile(source, temporary_media)
            with temporary_media.open("ab") as copied:
                copied.flush()
                os.fsync(copied.fileno())
            _write_json(stage / "proxy.json", payload)
            os.replace(temporary_media, self._path(key, "mp4"))
            os.replace(stage / "proxy.json", self._path(key, "json"))
        finally:
            for name in ("proxy.mp4", "proxy.json"):
                (stage / name).unlink(missing_ok=True)
            stage.rmdir()
        return self._path(key, "mp4")

    def scene(self, timeline, scene_id):
        clip = next((value for value in timeline.clips if value.media.scene_id == scene_id), None)
        if clip is None:
            raise ValueError("Scene is not present in the selected timeline.")
        key = content_fingerprint({"version": 1, "kind": "scene_preview",
                                   "timeline_id": timeline.id, "scene_id": scene_id})
        image, audio, manifest_path = self._path(key, "png"), self._path(key, "wav"), self._path(key, "scene.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
            if manifest is not None and (manifest.get("version") != 1 or manifest.get("cache_key") != key):
                manifest = None
        except (OSError, ValueError, TypeError):
            manifest = None
        if manifest is not None and manifest.get("kind") == "scene_preview":
            try:
                valid = (manifest["timeline_id"] == timeline.id and manifest["scene_id"] == scene_id
                         and image.is_file() and audio.is_file()
                         and _checksum(image) == manifest["image_checksum"]
                         and _checksum(audio) == manifest["audio_checksum"])
                if valid:
                    return ScenePreview(timeline.id, scene_id, image, audio,
                                        manifest["sample_rate"], manifest["frame_count"])
            except (OSError, KeyError, TypeError):
                pass

        render_root, staged = self.render_media.stage_scene(timeline, scene_id)
        source_image = contained_path(render_root, "image-0.png")
        source_audio = contained_path(render_root, "audio-0.wav")
        stage = Path(tempfile.mkdtemp(prefix="scene-", dir=self.root))
        try:
            temporary_image, temporary_audio = stage / "scene.png", stage / "scene.wav"
            shutil.copyfile(source_image, temporary_image)
            span = staged.media.audio
            with wave.open(str(source_audio), "rb") as reader:
                if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getnframes(),
                        reader.getcomptype()) != (1, 2, span.sample_rate, span.frame_count, "NONE"):
                    raise ValueError("Scene preview requires the verified PCM narration source.")
                reader.setpos(span.start_sample)
                frames = reader.readframes(span.end_sample - span.start_sample)
                if len(frames) != (span.end_sample - span.start_sample) * 2:
                    raise ValueError("Narration ended before the selected scene range.")
                with wave.open(str(temporary_audio), "wb") as writer:
                    writer.setparams((1, 2, span.sample_rate, 0, "NONE", "not compressed"))
                    writer.writeframes(frames)
            payload = {"version": 1, "kind": "scene_preview", "cache_key": key,
                       "timeline_id": timeline.id, "scene_id": scene_id,
                       "sample_rate": span.sample_rate, "frame_count": span.end_sample - span.start_sample,
                       "image_checksum": _checksum(temporary_image), "audio_checksum": _checksum(temporary_audio)}
            _write_json(stage / "scene.json", payload)
            os.replace(temporary_image, image)
            os.replace(temporary_audio, audio)
            os.replace(stage / "scene.json", manifest_path)
        finally:
            for name in ("scene.png", "scene.wav", "scene.json"):
                (stage / name).unlink(missing_ok=True)
            stage.rmdir()
        return ScenePreview(timeline.id, scene_id, image, audio, span.sample_rate,
                            span.end_sample - span.start_sample)
