"""Check prompt assets in the declared Docker COPY layout without provider calls."""
from pathlib import Path
import shutil

import pytest
from app import translation


def test_prompt_in_docker_copy_layout(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    # Materialize local COPY instructions; this is not a Docker-engine build test.
    for line in (root / 'Dockerfile').read_text().splitlines():
        if not line.startswith('COPY '):
            continue
        _, source, destination = line.split()
        source_path = root / source
        target = tmp_path / destination
        if source_path.is_dir():
            shutil.copytree(source_path, target)
        else:
            if destination == '.' or destination.endswith('/'):
                target = target / source_path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target)
    ignore = (root / '.dockerignore').read_text().splitlines()
    assert '!docs/' in ignore
    assert '!docs/translation-glossary-draft.md' in ignore
    monkeypatch.setattr(translation, 'ROOT', tmp_path)
    glossary = (tmp_path / 'docs/translation-glossary-draft.md').read_text(encoding='utf-8')
    assert glossary.strip() and glossary in translation.system_prompt()


def test_missing_glossary_returns_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(translation, 'ROOT', tmp_path)
    with pytest.raises(translation.ProviderError) as error:
        translation.system_prompt()
    assert error.value.code == 'missing_glossary'
    assert error.value.status == 503
