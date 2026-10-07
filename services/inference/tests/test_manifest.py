import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from app.errors import ServiceError
from app.manifest import load_manifest, materialize
from app.taxonomy import Taxonomy


def test_locked_manifest_and_unannounced_mutation(locked_settings):
    assert load_manifest(locked_settings)["manifestVersion"] == "unit-test-model-v1"
    path = Path(locked_settings.manifest_path)
    path.write_text(path.read_text() + " ")
    with pytest.raises(ServiceError) as error:
        load_manifest(locked_settings)
    assert error.value.code == "MODEL_NOT_CONFIGURED"


def test_branch_revisions_are_rejected_even_with_updated_digest(locked_settings):
    path = Path(locked_settings.manifest_path)
    data = json.loads(path.read_text())
    data["detector"]["revision"] = "main"
    path.write_text(json.dumps(data))
    settings = replace(locked_settings, manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ServiceError):
        load_manifest(settings)


def test_cached_model_bytes_are_verified(locked_settings):
    model = load_manifest(locked_settings)["detector"]
    directory = Path(locked_settings.model_cache_dir) / model["adapter"] / model["revision"]
    directory.mkdir(parents=True)
    (directory / "model.safetensors").write_bytes(b"not-real-weights-only-a-checksum-test")
    assert materialize(model, locked_settings, time.monotonic() + 1) == directory


def test_taxonomy_mutation_fails(locked_settings):
    with pytest.raises(ServiceError) as error:
        Taxonomy(locked_settings.taxonomy_path, "taxonomy-2026-10-07-v1", "0" * 64)
    assert error.value.code == "TAXONOMY_INTEGRITY_FAILED"
