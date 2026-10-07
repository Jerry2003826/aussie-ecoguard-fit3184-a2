import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("oracle_config", Path(__file__).parents[1] / "validate-config.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DeploymentGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "models"
        self.cache.mkdir()
        self.taxonomy = self.root / "taxonomy.json"
        self.taxonomy.write_bytes(b'{"species":[]}')
        self.manifest = {"schemaVersion": "1", "status": "locked", "entryModel": "aws-rekognition",
                         "manifestVersion": "test-only", "taxonomySha256": MODULE.sha256(self.taxonomy)}
        for role, adapter in (("detector", "grounding-dino"), ("embedding", "siglip2")):
            directory = self.cache / adapter / ("a" * 40)
            directory.mkdir(parents=True)
            (directory / "test.safetensors").write_bytes(b"test fixture, not model weights")
            self.manifest[role] = {"adapter": adapter, "revision": "a" * 40,
                                   "files": [{"name": "test.safetensors", "bytes": (directory / "test.safetensors").stat().st_size,
                                              "sha256": MODULE.sha256(directory / "test.safetensors")} ]}
        self.manifest_path = self.root / "model.locked.json"
        self.env = self.root / "inference.env"
        self.env_values = {
            "SERVICE_AUTH_MODE": "oracle",
            "SERVICE_TOKEN_AUDIENCE": "https://inference.course.test/ecoguard-inference",
            "SERVICE_TOKEN_EMAIL": "ecoguard-invoker@ecoguard-project.iam.gserviceaccount.com",
            "COGNITO_ISSUER": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Test123",
            "COGNITO_CLIENT_ID": "0123456789abcdefghij",
            "SOURCE_S3_HOSTS": "ecoguard-test.s3.us-east-1.amazonaws.com",
            "SOURCE_KEY_PREFIXES": "derived/,queries/",
            "MODEL_MANIFEST_PATH": "/config/model.locked.json",
            "TAXONOMY_PATH": "/config/taxonomy.json", "MODEL_CACHE_DIR": "/models",
            "INFERENCE_TIMEOUT_SECONDS": "330", "TORCH_THREADS": "4",
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
        self.write_config()

    def write_config(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.env_values["MODEL_MANIFEST_SHA256"] = MODULE.sha256(self.manifest_path)
        self.env.write_text("\n".join(f"{key}={value}" for key, value in self.env_values.items()), encoding="utf-8")
        self.env.chmod(0o600)

    def validate(self):
        return MODULE.validate(self.env, self.manifest_path, self.taxonomy, self.cache)

    def test_complete_fixture_passes_without_claiming_cloud_acceptance(self):
        result = self.validate()
        self.assertEqual(result["artifacts"], 2)
        self.assertFalse(result["cloudAcceptance"])

    def test_modified_model_is_blocked(self):
        artifact = next(self.cache.rglob("*.safetensors"))
        artifact.write_bytes(b"x" * artifact.stat().st_size)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.validate()

    def test_manifest_digest_change_is_blocked(self):
        self.manifest_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest SHA-256 mismatch"):
            self.validate()

    def test_taxonomy_change_is_blocked(self):
        self.taxonomy.write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "Taxonomy SHA-256 mismatch"):
            self.validate()

    def test_cache_path_traversal_is_blocked(self):
        self.manifest["detector"]["files"][0]["name"] = "../outside.safetensors"
        self.write_config()
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            self.validate()

    def test_cloud_run_or_static_auth_cannot_be_enabled(self):
        self.env_values["SERVICE_AUTH_MODE"] = "cloud-run"
        self.write_config()
        with self.assertRaisesRegex(ValueError, "must be oracle"):
            self.validate()
        self.env_values["SERVICE_AUTH_MODE"] = "oracle"
        self.env_values["LOCAL_SERVICE_TOKEN"] = "test-only-token"
        self.write_config()
        with self.assertRaisesRegex(ValueError, "Do not configure LOCAL_SERVICE_TOKEN"):
            self.validate()

    def test_http_audience_and_expanded_s3_scope_are_blocked(self):
        self.env_values["SERVICE_TOKEN_AUDIENCE"] = "http://inference.course.test"
        self.write_config()
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            self.validate()
        self.env_values["SERVICE_TOKEN_AUDIENCE"] = "https://inference.course.test"
        self.env_values["SOURCE_KEY_PREFIXES"] = ""
        self.write_config()
        with self.assertRaisesRegex(ValueError, "SOURCE_KEY_PREFIXES"):
            self.validate()

    def test_shell_expressions_are_not_evaluated(self):
        self.env_values["TORCH_THREADS"] = "$(echo 4)"
        self.write_config()
        with self.assertRaisesRegex(ValueError, "Shell expansion"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
