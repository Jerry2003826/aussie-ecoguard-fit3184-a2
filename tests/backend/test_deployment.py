"""Offline SDK/serialization checks; these do not claim live cloud acceptance."""
import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import boto3
from botocore.stub import Stubber, ANY
from botocore.validate import validate_parameters
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages/domain"), str(ROOT / "services/aws")]
from ecoguard_aws.config import Config
from ecoguard_aws.media_processing import Preprocessor
from ecoguard_aws.repository import DynamoRepository, Write, Conflict
from ecoguard_aws.storage import S3Storage, MemoryStorage, checksum_header

spec = importlib.util.spec_from_file_location("deploy_lex", ROOT / "services/aws/tools/deploy_lex.py")
lex_tool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lex_tool
spec.loader.exec_module(lex_tool)


def offline_session():
    return boto3.Session(aws_access_key_id="offline-test-key", aws_secret_access_key="offline-test-secret", aws_session_token="offline-test-session", region_name="us-east-1")


class AdapterTests(unittest.TestCase):
    def test_s3_signing_binds_checksum_size_mime_and_expiry(self):
        storage = S3Storage(replace(Config(), media_bucket="offline-test-bucket"), offline_session())
        digest = hashlib.sha256(b"image").hexdigest()
        signed = storage.sign_upload("incoming/library/id", "image/png", digest, 5)
        query = parse_qs(urlparse(signed["uploadUrl"]).query)
        self.assertEqual(query["X-Amz-Expires"], ["600"])
        shortened = storage.sign_upload("incoming/query/id", "image/png", digest, 5, 37)
        self.assertEqual(parse_qs(urlparse(shortened["uploadUrl"]).query)["X-Amz-Expires"], ["37"])
        self.assertEqual(set(query["X-Amz-SignedHeaders"][0].split(";")), {"content-length", "content-type", "host", "x-amz-checksum-sha256"})
        self.assertEqual(signed["headers"]["x-amz-checksum-sha256"], checksum_header(digest))
        with Stubber(storage.client) as stub:
            stub.add_response("head_object", {"ContentLength": 5, "ChecksumSHA256": checksum_header(digest), "ContentType": "image/png"}, {"Bucket": "offline-test-bucket", "Key": "incoming/library/id", "ChecksumMode": "ENABLED"})
            self.assertEqual(storage.head("incoming/library/id")["sha256"], digest)

    def test_dynamo_transaction_serialization_and_conditional_conflict(self):
        repo = DynamoRepository(Config(), offline_session())
        expected = {"TransactItems": [{"Put": {"TableName": "ecoguard-media", "Item": {"mediaId": {"S": "m1"}, "score": {"N": "0.3"}, "_rev": {"N": "3"}}, "ConditionExpression": "#rev = :rev", "ExpressionAttributeNames": {"#rev": "_rev"}, "ExpressionAttributeValues": {":rev": {"N": "2"}}}}]}
        with Stubber(repo.client) as stub:
            stub.add_response("transact_write_items", {}, expected)
            repo.transact([Write("media", {"mediaId": "m1", "score": 0.3}, expected=2)])
            stub.add_client_error("transact_write_items", service_error_code="TransactionCanceledException", modeled_fields={"CancellationReasons": [{"Code": "ConditionalCheckFailed"}]}, expected_params=expected)
            with self.assertRaises(Conflict):
                repo.transact([Write("media", {"mediaId": "m1", "score": 0.3}, expected=2)])

    def test_real_image_decode_and_rekognition_sdk_request(self):
        image = io.BytesIO()
        Image.new("RGB", (1800, 600), (90, 120, 30)).save(image, "PNG")
        data = image.getvalue()
        cfg = replace(Config(), mode="local")
        storage = MemoryStorage(cfg)
        storage.put("incoming/library/job", data, "image/png")
        client = offline_session().client("rekognition")
        with Stubber(client) as stub:
            # SDK response is explicitly synthetic; JPEG transformation is real.
            stub.add_response("detect_labels", {"Labels": [], "LabelModelVersion": "offline-model-fixture"}, {"Image": {"Bytes": ANY}, "MaxLabels": 50, "MinConfidence": 65, "Features": ["GENERAL_LABELS"]})
            result = Preprocessor(cfg, storage, client).process({"sourceKey": "incoming/library/job", "operationId": "job", "mediaId": "media", "generation": 1, "fileType": "image", "contentType": "image/png", "sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        frame = result["frames"][0]
        content = storage.read(frame["key"])
        self.assertEqual(hashlib.sha256(content).hexdigest(), frame["sha256"])
        with Image.open(io.BytesIO(content)) as decoded:
            self.assertEqual(decoded.format, "JPEG")
            self.assertEqual(decoded.width, 1600)
        self.assertEqual(frame["entryLabels"], [])


class LexRecordingClient:
    """No network: validates every deploy request against installed AWS SDK models."""
    def __init__(self, service, session, owner):
        self.client = session.client(service)
        self.meta, self.exceptions = self.client.meta, self.client.exceptions
        self.owner, self.service = owner, service

    def __getattr__(self, name):
        def call(**args):
            operation = self.meta.method_to_api_mapping[name]
            validate_parameters(args, self.meta.service_model.operation_model(operation).input_shape)
            self.owner.calls.append((self.service, name, args))
            if self.service == "sts":
                return {"Account": "123456789012"}
            if self.service == "iam":
                return {"Role": {"Arn": self.owner.settings.role_arn, "AssumeRolePolicyDocument": {"Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole", "Principal": {"Service": "lexv2.amazonaws.com"}}]}}}
            if self.service == "lambda":
                return {}
            if name == "list_bots":
                return {"botSummaries": []}
            if name == "create_bot":
                return {"botId": "BOTID00001"}
            if name in ("describe_bot", "describe_bot_version"):
                return {"botStatus": "Available"}
            if name == "list_bot_locales":
                return {"botLocaleSummaries": []}
            if name == "describe_bot_locale":
                return {"botLocaleStatus": "Built"}
            if name == "list_slot_types":
                return {"slotTypeSummaries": []}
            if name in ("create_slot_type", "update_slot_type"):
                return {"slotTypeId": "SLOTTYPE01" if args["slotTypeName"] == "EcoGuardSpecies" else "SLOTTYPE02"}
            if name == "list_intents":
                # Lex creates a fallback intent automatically for new locales.
                return {"intentSummaries": [{"intentName": "FallbackIntent", "intentId": "FALLBACK01"}]}
            if name == "create_intent":
                return {"intentId": {"SearchAnimals": "SEARCH0001", "FindPet": "FINDPET001", "EcoGuardHelp": "HELP000001"}[args["intentName"]]}
            if name == "list_slots":
                return {"slotSummaries": []}
            if name == "create_slot":
                return {"slotId": {"species": "SPECIES001", "count": "COUNT00001", "color": "COLOR00001", "date": "DATE000001"}[args["slotName"]]}
            if name == "create_bot_version":
                return {"botVersion": "1"}
            if name == "list_bot_aliases":
                return {"botAliasSummaries": []}
            if name == "create_bot_alias":
                return {"botAliasId": "ALIAS00001"}
            if name == "describe_bot_alias":
                return {"botAliasStatus": "Available"}
            return {}
        return call


class LexRecordingSession:
    def __init__(self, settings):
        self.settings, self.calls = settings, []
        self.session = offline_session()

    def client(self, service, **kwargs):
        return LexRecordingClient(service, self.session, self)


class LexDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.cfg = lex_tool.Settings("us-east-1", "arn:aws:iam::123456789012:role/ApprovedLexRole", "arn:aws:lambda:us-east-1:123456789012:function:ecoguard-lex")

    def test_plan_never_initializes_aws(self):
        with patch("boto3.Session", side_effect=AssertionError("Plan must not contact AWS")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lex_tool.main(["--role-arn", self.cfg.role_arn, "--fulfillment-arn", self.cfg.fulfillment_arn]), 0)

    def test_complete_deploy_requests_are_sdk_valid_and_iam_read_only(self):
        recording = LexRecordingSession(self.cfg)
        result = lex_tool.Deployer(self.cfg, recording, sleep=lambda _: None, progress=lambda _: None).apply()
        self.assertEqual(result["environment"]["ECO_LEX_ALIAS_ID"], "ALIAS00001")
        self.assertEqual([name for service, name, _ in recording.calls if service == "iam"], ["get_role"])
        permission = next(args for service, name, args in recording.calls if name == "add_permission")
        self.assertEqual(permission["SourceArn"], "arn:aws:lex:us-east-1:123456789012:bot-alias/BOTID00001/ALIAS00001")
        intents = [args for _, name, args in recording.calls if name == "update_intent"]
        self.assertEqual({i["intentName"] for i in intents}, set(lex_tool.INTENTS))
        self.assertTrue(all(i["fulfillmentCodeHook"]["enabled"] and not i["dialogCodeHook"]["enabled"] for i in intents))
        created_intents = [args["intentName"] for _, name, args in recording.calls if name == "create_intent"]
        self.assertIn("EcoGuardHelp", created_intents)
        self.assertNotIn("HelpIntent", created_intents)  # Reserved built-in name.
        self.assertNotIn("FallbackIntent", created_intents)  # Reuse auto-created fallback.
        fallback = next(i for i in intents if i.get("parentIntentSignature") == "AMAZON.FallbackIntent")
        self.assertNotIn("sampleUtterances", fallback)
        self.assertNotIn("slotPriorities", fallback)
        slots = [args for _, name, args in recording.calls if name == "create_slot"]
        for intent in (i for i in intents if i["intentName"] in ("SearchAnimals", "FindPet")):
            intent_slots = [s for s in slots if s["intentId"] == intent["intentId"]]
            self.assertEqual(len(intent["slotPriorities"]), len(intent_slots))
            self.assertEqual({p["priority"] for p in intent["slotPriorities"]}, set(range(1, len(intent_slots)+1)))
            self.assertEqual({p["slotId"] for p in intent["slotPriorities"]}, {"SPECIES001", "COUNT00001", "COLOR00001", "DATE000001"})
            self.assertEqual({s["slotName"] for s in intent_slots if s["valueElicitationSetting"]["slotConstraint"] == "Required"}, {"species"})
        search = next(i for i in intents if i["intentName"] == "SearchAnimals")
        self.assertTrue(any("{" not in u["utterance"] for u in search["sampleUtterances"]), "Search must be reachable without a species before Lex can elicit the required slot.")

    def test_account_region_mismatch_stops_before_aws(self):
        with self.assertRaises(ValueError):
            lex_tool.plan(replace(self.cfg, region="us-west-2"))

    def test_slot_resolution_matches_aws_enum_for_create_and_update(self):
        # Botocore validate_parameters checks shape types, but not enum values.
        # The live API rejected TOP_RESOLUTION despite the shape-only test above.
        common = {"botId": "BOTID00001", "botVersion": "DRAFT", "localeId": "en_US"}
        for existing in (False, True):
            with self.subTest(existing=existing):
                recording = LexRecordingSession(self.cfg)
                deployer = lex_tool.Deployer(self.cfg, recording)
                found = [{"slotTypeName": "EcoGuardSpecies", "slotTypeId": "SLOTTYPE01"}] if existing else []
                with patch.object(deployer, "_all", return_value=iter(found)):
                    deployer._slot_type(common, "EcoGuardSpecies", lex_tool.SPECIES)
                operation = "UpdateSlotType" if existing else "CreateSlotType"
                shape = deployer.lex.meta.service_model.operation_model(operation).input_shape
                valid_values = shape.members["valueSelectionSetting"].members["resolutionStrategy"].enum
                request = recording.calls[-1][2]
                self.assertIn(request["valueSelectionSetting"]["resolutionStrategy"], valid_values)
                self.assertNotIn("TOP_RESOLUTION", valid_values)


if __name__ == "__main__":
    unittest.main()
