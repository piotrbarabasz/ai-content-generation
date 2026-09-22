"""Explicit application-source closure shipped into the private Piper interpreter."""

from importlib.resources import files
import json


# Provider adapters are pure Python and lazy; their optional runtimes are absent.
# This list is also consumed by the standalone smoke build, avoiding two bundles.
MODULES = """
domain/__init__ domain/base domain/content_brief domain/dependencies domain/enums
domain/export_config domain/generation_job domain/narrative_segment domain/project
domain/provider_config domain/publication domain/types domain/workflow_config domain/speech_boundary
jobs/__init__ jobs/coordinator providers/__init__ providers/chatterbox_v3
providers/interfaces providers/image_generation providers/mock_tts providers/piper_catalog providers/piper_tts
providers/registry providers/tts_capabilities providers/tts_catalog providers/tts_factory
providers/tts_result providers/tts_settings providers/xtts_v2 runtime/__init__
runtime/model_index runtime/native_piper runtime/piper_health runtime/piper_worker
runtime/profile_catalog runtime/profiles runtime/protocol runtime/provisioning runtime/resources
runtime/section_synthesis runtime/section_voice runtime/section_worker runtime/supervisor
runtime/voice_http runtime/windows_job runtime/worker runtime/worker_bundle
runtime/chatterbox_profile runtime/chatterbox_assets runtime/chatterbox_health
runtime/chatterbox_voice runtime/chatterbox_worker
storage/__init__ storage/manifest storage/paths tts/__init__ tts/assembly tts/catalog
tts/chunk_synthesis tts/chunking tts/manifest tts/post_processing tts/selection
""".split()


def source_files():
    names = {"app/__init__.py", "app/runtime/piper_cpu_windows_x64.json",
             *("app/" + module + ".py" for module in MODULES)}
    root = files("app")
    packed = root.joinpath("runtime/worker_sources.json")
    if packed.is_file():
        payload = json.loads(packed.read_text(encoding="utf-8"))
        if set(payload) != names or any(not isinstance(value, str) for value in payload.values()):
            raise ValueError("Invalid bundled worker source closure.")
        return {name: value.encode("utf-8") for name, value in payload.items()}
    return {name: root.joinpath(name.removeprefix("app/")).read_bytes() for name in sorted(names)}
