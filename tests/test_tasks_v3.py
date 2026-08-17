import pytest

from forge.protocol import load_protocol
from forge.tasks import TaskManifestError, search_visible_manifest, validate_task_manifest


def _manifest():
    families = [f"family-{i}" for i in range(10)]
    return {
        "manifest_id": "tasks-v3-test",
        "sealed": True,
        "hidden_content_in_search_bundle": False,
        "development_problems": ["dev-1"],
        "development_metadata": [{"problem_family": "dev-family"}],
        "holdout_problems": [
            {
                "problem_id": f"h-{i}",
                "problem_family": families[i],
                "external_repository_pack": i < 5,
                "search_instance_clusters": 50,
                "test_instance_clusters": 100,
                "hidden_test_instances": 500,
                "distributions": ["iid_heldout", "size_shift", "distribution_shift"],
            }
            for i in range(10)
        ],
    }


def test_task_manifest_enforces_holdout_structure_and_public_view():
    manifest = _manifest()
    validate_task_manifest(manifest, require_sealed=True)
    visible = search_visible_manifest(manifest)
    assert visible["holdout_problems"][0]["problem_id"] == "h-0"
    assert "hidden_test_instances" not in visible["holdout_problems"][0]


def test_task_manifest_rejects_overlap_and_unsealed_holdout():
    manifest = _manifest()
    manifest["holdout_problems"][0]["problem_id"] = "dev-1"
    with pytest.raises(TaskManifestError):
        validate_task_manifest(manifest)
    manifest = _manifest()
    manifest["sealed"] = False
    with pytest.raises(TaskManifestError):
        validate_task_manifest(manifest, require_sealed=True)


def test_task_manifest_rejects_small_or_narrow_holdout():
    manifest = _manifest()
    manifest["holdout_problems"] = manifest["holdout_problems"][:9]
    with pytest.raises(TaskManifestError):
        validate_task_manifest(manifest)
    manifest = _manifest()
    manifest["holdout_problems"][0]["hidden_test_instances"] = 1
    with pytest.raises(TaskManifestError):
        validate_task_manifest(manifest)


def test_task_manifest_rejects_non_object_or_malformed_holdout_entries():
    manifest = _manifest()
    manifest["holdout_problems"][0] = "not-an-object"
    with pytest.raises(TaskManifestError):
        validate_task_manifest(manifest)
    manifest = _manifest()
    manifest["holdout_problems"][0]["distributions"] = "iid"
    with pytest.raises(TaskManifestError):
        validate_task_manifest(manifest)


def test_task_manifest_rejects_unknown_distribution_and_non_boolean_pack_flag():
    manifest = _manifest()
    manifest["holdout_problems"][0]["distributions"] = ["iid_heldout", "unknown_shift"]
    with pytest.raises(TaskManifestError):
        validate_task_manifest(manifest)


def test_task_manifest_requires_identity_and_boolean_sealing_fields():
    manifest = _manifest()
    manifest.pop("manifest_id")
    with pytest.raises(TaskManifestError):
        validate_task_manifest(manifest)
    manifest = _manifest()
    manifest["sealed"] = "yes"
    with pytest.raises(TaskManifestError):
        validate_task_manifest(manifest)
    manifest = _manifest()
    manifest["holdout_problems"][0]["external_repository_pack"] = "false"
    with pytest.raises(TaskManifestError):
        validate_task_manifest(manifest)


def test_search_visible_manifest_requires_explicit_hidden_content_absence():
    manifest = _manifest()
    manifest["hidden_content_in_search_bundle"] = True
    with pytest.raises(TaskManifestError, match="hidden_content_in_search_bundle=false"):
        search_visible_manifest(manifest)


def test_task_manifest_can_be_bound_to_the_frozen_protocol_problem_set():
    manifest = _manifest()
    protocol = load_protocol()
    manifest["development_problems"] = list(protocol["development_problems"])
    validate_task_manifest(manifest, require_sealed=True, protocol_spec=protocol)

    manifest["development_problems"][0] = "different-dev"
    with pytest.raises(TaskManifestError, match="differ from frozen protocol"):
        validate_task_manifest(manifest, require_sealed=True, protocol_spec=protocol)

    manifest = _manifest()
    manifest.pop("hidden_content_in_search_bundle")
    with pytest.raises(TaskManifestError, match="hidden_content_in_search_bundle=false"):
        search_visible_manifest(manifest)
