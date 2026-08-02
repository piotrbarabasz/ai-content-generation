from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_chatterbox_runtime_is_an_optional_pinned_extra() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")

    assert '[project.optional-dependencies]' in pyproject
    assert 'chatterbox-v3 = [' in pyproject
    assert 'chatterbox-tts==0.1.7' in pyproject
    assert 'resemble-perth==1.0.1' in pyproject
    assert 'setuptools<81' in pyproject
    declared_requirements = [
        line.strip().lower()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    assert not any('chatterbox-tts' in line for line in declared_requirements)
    assert not any(line.startswith('torch') for line in declared_requirements)
    assert not any(line.startswith('torchaudio') for line in declared_requirements)


def test_chatterbox_docs_and_runtime_hygiene_are_recorded() -> None:
    setup = (ROOT / "docs" / "tts" / "CHATTERBOX_SETUP.md").read_text(encoding="utf-8")
    spike = (ROOT / "docs" / "tts" / "CHATTERBOX_MANUAL_SPIKE.md").read_text(encoding="utf-8")
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert 'Python 3.11' in setup
    assert 'torch==2.6.0+cu124' in setup
    assert 'torchaudio==2.6.0+cu124' in setup
    assert '5de7a54aa4e5e2baadb0182dde554908b48b85c2' in setup
    assert 'built-in voice' in setup
    assert 'speaker-reference cloning' in setup.lower()
    assert 'manual_quality_result: "PASS"' in spike
    for ignored_path in ('.runtime/', '.cache/huggingface/', 'voice-references/', 'speaker-references/', '*.wav'):
        assert ignored_path in ignore
