"""Offline checks for source and installed environment bootstrap."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

import app.environment as environment
from app.desktop.image_composition import compose_installed_image, compose_installed_upscale
from app.desktop.llm_composition import compose_installed_llm


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    module = tmp_path / "backend" / "app" / "environment.py"
    module.parent.mkdir(parents=True)
    monkeypatch.setattr(environment, "__file__", str(module))
    monkeypatch.delenv("AICS_ENV_FILE", raising=False)
    return tmp_path


def test_optional_source_file_and_precedence(checkout, monkeypatch, capsys):
    assert environment.load_application_environment() is None
    path = checkout / ".env"
    path.write_text('AICS_LOCAL_IMAGE_ROOT="D:/AI Content Studio/local image"\n'
                    'AICS_OPENAI_MODEL=model-from-file\n'
                    'OPENAI_API_KEY=fixture-secret\n', encoding="utf-8")
    monkeypatch.setenv("AICS_OPENAI_MODEL", "process-model")
    for name in ("AICS_LOCAL_IMAGE_ROOT", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert environment.load_application_environment() == path
    assert environment.load_application_environment() == path
    assert os.environ["AICS_LOCAL_IMAGE_ROOT"] == "D:/AI Content Studio/local image"
    assert os.environ["AICS_OPENAI_MODEL"] == "process-model"
    assert os.environ["OPENAI_API_KEY"] == "fixture-secret"
    assert "fixture-secret" not in capsys.readouterr().out


def test_explicit_file_and_missing_file(checkout, tmp_path, monkeypatch):
    path = tmp_path / "another.env"
    path.write_text("AICS_D061_MARKER=explicit\nAICS_ENV_FILE=ignored.env\n", encoding="utf-8")
    monkeypatch.setenv("AICS_ENV_FILE", str(path))
    assert environment.load_application_environment() == path
    assert os.environ["AICS_D061_MARKER"] == "explicit"
    assert os.environ["AICS_ENV_FILE"] == str(path)
    monkeypatch.setenv("AICS_ENV_FILE", str(tmp_path / "missing.env"))
    with pytest.raises(FileNotFoundError, match="AICS_ENV_FILE"):
        environment.load_application_environment()


def test_llm_key_is_available_but_not_in_identity(checkout, monkeypatch):
    (checkout / ".env").write_text("AICS_LLM_PROVIDER=openai\nAICS_OPENAI_MODEL=fixture-model\n"
                                     "AICS_OPENAI_API_KEY_ENV=D061_TEST_KEY\nD061_TEST_KEY=fixture-secret\n")
    for name in ("AICS_LLM_PROVIDER", "AICS_OPENAI_MODEL", "AICS_OPENAI_API_KEY_ENV", "D061_TEST_KEY"):
        monkeypatch.delenv(name, raising=False)
    environment.load_application_environment()
    provider = compose_installed_llm()
    assert provider.settings.model == "fixture-model"
    assert provider.settings.api_key_env == "D061_TEST_KEY"
    assert provider._environment["D061_TEST_KEY"] == "fixture-secret"
    assert "fixture-secret" not in str(provider.generation_identity())


def test_existing_composition_reads_loaded_settings(checkout, monkeypatch):
    (checkout / ".env").write_text("AICS_LLM_PROVIDER=openai\nAICS_OPENAI_MODEL=fixture-model\n"
                                     "AICS_OPENAI_TIMEOUT_SECONDS=42\nAICS_IMAGE_PROVIDER=local\n"
                                     "AICS_LOCAL_IMAGE_ROOT=D:/local image\nAICS_UPSCALE_PROVIDER=local\n"
                                     "AICS_LOCAL_UPSCALE_ROOT=D:/local upscale\n")
    names = ("AICS_LLM_PROVIDER", "AICS_OPENAI_MODEL", "AICS_OPENAI_TIMEOUT_SECONDS",
             "AICS_IMAGE_PROVIDER", "AICS_LOCAL_IMAGE_ROOT", "AICS_UPSCALE_PROVIDER", "AICS_LOCAL_UPSCALE_ROOT")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    environment.load_application_environment()
    assert compose_installed_llm().settings.timeout_seconds == 42
    import app.desktop.image_composition as images
    roots = []
    class FakeProvider:
        def capabilities(self):
            return None
    monkeypatch.setattr(images, "build_image_provider", lambda name, *, settings: roots.append(settings["root"]) or FakeProvider())
    monkeypatch.setattr("app.providers.local_upscale.LocalUpscaleProvider", lambda root: roots.append(root) or FakeProvider())
    compose_installed_image()
    compose_installed_upscale()
    assert roots == ["D:/local image", "D:/local upscale"]
    monkeypatch.setenv("AICS_OPENAI_TIMEOUT_SECONDS", "bad")
    with pytest.raises(ValueError, match="AICS_OPENAI_TIMEOUT_SECONDS"):
        compose_installed_llm()
    monkeypatch.setenv("AICS_IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("AICS_OPENAI_IMAGE_MODEL", "fixture-image")
    monkeypatch.setenv("AICS_OPENAI_IMAGE_MAX_RETRIES", "bad")
    with pytest.raises(ValueError, match="AICS_OPENAI_IMAGE_MAX_RETRIES"):
        compose_installed_image()


def test_launcher_bootstraps_backend_and_reports_missing_runtime(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    env_file = tmp_path / "launcher.env"
    env_file.write_text("AICS_CHATTERBOX_RUNTIME_ROOT=Z:/absent-runtime\n"
                        "AICS_CHATTERBOX_MODEL_ROOT=Z:/absent-models\n"
                        "AICS_CHATTERBOX_CACHE_ROOT=Z:/cache\n")
    process = os.environ.copy()
    process.pop("PYTHONPATH", None)
    for name in ("AICS_CHATTERBOX_RUNTIME_ROOT", "AICS_CHATTERBOX_MODEL_ROOT", "AICS_CHATTERBOX_CACHE_ROOT"):
        process.pop(name, None)
    process["AICS_ENV_FILE"] = str(env_file)
    result = subprocess.run([sys.executable, str(repo / "run_test_chatterbox.py")],
                            cwd=tmp_path, env=process, text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "AICS_CHATTERBOX_RUNTIME_ROOT directory does not exist" in result.stderr
    assert "No module named 'app'" not in result.stderr


def test_launcher_process_path_overrides_dotenv(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    env_file = tmp_path / "launcher.env"
    models = tmp_path / "models"
    models.mkdir()
    env_file.write_text("AICS_CHATTERBOX_RUNTIME_ROOT=Z:/missing-from-file\n"
                        f"AICS_CHATTERBOX_MODEL_ROOT={models.as_posix()}\n"
                        "AICS_CHATTERBOX_CACHE_ROOT=Z:/cache\n")
    runtime = tmp_path / "runtime override"
    runtime.mkdir()
    process = os.environ.copy()
    process.pop("PYTHONPATH", None)
    process["AICS_ENV_FILE"] = str(env_file)
    process["AICS_CHATTERBOX_RUNTIME_ROOT"] = str(runtime)
    for name in ("AICS_CHATTERBOX_MODEL_ROOT", "AICS_CHATTERBOX_CACHE_ROOT"):
        process.pop(name, None)
    result = subprocess.run([sys.executable, str(repo / "run_test_chatterbox.py")],
                            cwd=tmp_path, env=process, text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "AICS_CHATTERBOX_RUNTIME_ROOT directory does not exist" not in result.stderr
    assert "Checking Chatterbox runtime" in result.stdout
    assert str(runtime) in result.stdout


def test_real_env_is_ignored_and_example_is_tracked():
    repo = Path(__file__).resolve().parents[3]
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert git("check-ignore", "--quiet", ".env").returncode == 0
    assert git("check-ignore", "--quiet", ".env.local").returncode == 0
    assert git("check-ignore", "--quiet", ".env.example").returncode == 1
    assert git("check-ignore", "--quiet", ".env.sample").returncode == 1
    assert git("ls-files", "--error-unmatch", ".env.example").returncode == 0


def test_desktop_main_loads_before_provider_composition(monkeypatch):
    import app.desktop.__main__ as desktop
    import app.desktop.llm_composition as llm
    import app.desktop.image_composition as images
    events = []
    monkeypatch.setattr(environment, "load_application_environment", lambda: events.append("load"))
    monkeypatch.setattr(llm, "compose_installed_llm", lambda: events.append("llm") or None)
    monkeypatch.setattr(images, "compose_installed_image", lambda: events.append("image") or None)
    monkeypatch.setattr(images, "compose_installed_upscale", lambda: events.append("upscale") or None)
    class FakeApplication:
        def __init__(self, args):
            pass
        def exec(self):
            return 0
    class FakeEditor:
        def __init__(self, *args, **kwargs):
            pass
        def show(self):
            pass
    monkeypatch.setattr(desktop, "QApplication", FakeApplication)
    monkeypatch.setattr(desktop, "ProjectEditor", FakeEditor)
    assert desktop.main() == 0
    assert events == ["load", "llm", "image", "upscale"]
