"""D041 installed-path and release-package contracts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

from app.desktop.deployment import APPLICATION_VERSION, UserDataPaths, media_executables


ROOT = Path(__file__).resolve().parents[3]
D041 = ROOT / "packaging/d041"


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


release = _module("d041_release", D041 / "release.py")
sys.modules["release"] = release
build = _module("d041_build", D041 / "build.py")


def test_user_data_is_absolute_and_separate_from_installation(tmp_path):
    local = tmp_path / "Użytkownik dane"
    paths = UserDataPaths.discover({"LOCALAPPDATA": str(local)}).prepare()
    assert paths.root == local / "AI Content Studio"
    assert all(path.is_dir() for path in (paths.runtimes, paths.models, paths.cache, paths.logs))
    with pytest.raises(RuntimeError, match="LOCALAPPDATA"):
        UserDataPaths.discover({})
    with pytest.raises(RuntimeError, match="absolute"):
        UserDataPaths.discover({"LOCALAPPDATA": "relative"})


def test_installed_audio_is_unavailable_without_active_private_runtime(tmp_path):
    from app.desktop.product_composition import compose_installed_audio, compose_installed_chatterbox_audio

    paths = UserDataPaths.discover({"LOCALAPPDATA": str(tmp_path)})
    assert compose_installed_audio(object(), paths=paths) is None
    assert compose_installed_chatterbox_audio(object(), paths=paths) is None


def test_packaged_media_never_falls_back_to_path(tmp_path):
    bundle = tmp_path / "Program pl"
    media = bundle / "media"
    media.mkdir(parents=True)
    (media / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (media / "ffprobe.exe").write_bytes(b"ffprobe")
    called = []
    ffmpeg, ffprobe = media_executables(root=bundle, path_lookup=lambda name: called.append(name))
    assert Path(ffmpeg) == (media / "ffmpeg.exe").resolve()
    assert Path(ffprobe) == (media / "ffprobe.exe").resolve()
    assert called == []
    (media / "ffprobe.exe").unlink()
    assert media_executables(root=bundle, path_lookup=lambda _name: "unsafe") == (None, None)


def test_component_lock_and_bundle_audit_reject_models_tests_and_secrets(tmp_path):
    lock = release.load_lock(D041 / "components.lock.json")
    assert lock["application"]["pyside6"] == "6.11.2"
    assert lock["application"]["version"] == APPLICATION_VERSION
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "AIContentStudio.exe").write_bytes(b"app")
    inventory = release.audit_bundle(bundle)
    assert inventory[0]["sha256"] == release.digest(bundle / "AIContentStudio.exe")
    for relative in ("tests/test_app.py", "models/voice.onnx", "private.key"):
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"forbidden")
        with pytest.raises(ValueError, match="Forbidden"):
            release.audit_bundle(bundle)
        path.unlink()


def test_ffmpeg_pin_requires_exact_files_sizes_and_hashes(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    first = media / "ffmpeg.exe"
    second = media / "ffprobe.exe"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    lock = {"ffmpeg": {"files": [
        {"name": first.name, "size": 3, "sha256": release.digest(first)},
        {"name": second.name, "size": 3, "sha256": release.digest(second)},
    ]}}
    assert release.verify_ffmpeg(media, lock) == (first.resolve(), second.resolve())
    second.write_bytes(b"bad")
    with pytest.raises(ValueError, match="release pin"):
        release.verify_ffmpeg(media, lock)


def test_manifest_records_unsigned_bundle_as_ineligible(tmp_path):
    lock = release.load_lock(D041 / "components.lock.json")
    destination = tmp_path / "release-manifest.json"
    release.write_manifest(destination, lock=lock, inventory=({"path": "app.exe"},), signed=False)
    manifest = json.loads(destination.read_text(encoding="utf-8"))
    assert manifest["signing"] == {"status": "unsigned", "release_eligible": False}


def test_installer_compiler_is_pinned_by_name_size_and_hash(tmp_path):
    compiler = tmp_path / "ISCC.exe"
    compiler.write_bytes(b"compiler")
    lock = {"installer": {"compiler_size": 8, "compiler_sha256": release.digest(compiler)}}
    assert release.verify_installer_compiler(compiler, lock) == compiler.resolve()
    compiler.write_bytes(b"changed!")
    with pytest.raises(ValueError, match="pinned"):
        release.verify_installer_compiler(compiler, lock)


def test_installer_is_per_user_and_does_not_delete_user_data(tmp_path):
    template = (D041 / "installer.iss.in").read_text(encoding="utf-8")
    rendered = build.render_installer(template, version_value="1.2.3",
                                      bundle=tmp_path / "bundle", output=tmp_path / "out",
                                      signed_uninstaller=False)
    assert "PrivilegesRequired=lowest" in rendered
    assert "DefaultDirName={localappdata}\\Programs\\AI Content Studio" in rendered
    assert "[UninstallDelete]" not in rendered
    assert "SignedUninstaller=no" in rendered
    assert "SignTool=" not in rendered
    signed = build.render_installer(template, version_value="1.2.3",
                                    bundle=tmp_path / "bundle", output=tmp_path / "out",
                                    signed_uninstaller=True)
    assert "SignedUninstaller=yes" in signed
    assert "SignTool=release" in signed


def test_dependency_groups_keep_api_and_tests_out_of_desktop_base():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    base = text.split("[project.optional-dependencies]", 1)[0]
    desktop = text.split("desktop =", 1)[1].split("api =", 1)[0]
    assert "fastapi" not in base.lower() and "pytest" not in base.lower()
    assert "PySide6==6.11.2" in desktop
    assert 'api = ["fastapi>=0.115"]' in text
    assert '"pytest>=8.0"' in text


def test_release_build_explicitly_includes_qt_multimedia_plugins():
    text = (D041 / "build.py").read_text(encoding="utf-8")
    assert '"--include-qt-plugins=multimedia"' in text
