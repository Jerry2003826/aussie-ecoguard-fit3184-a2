"""Deploy the real Lex V2 bot; default mode prints a plan without contacting AWS.

This tool never creates roles, attaches IAM policies, or changes role trust. An
administrator must provide a role already trusted by lexv2.amazonaws.com, and
the caller must have Lex deployment, iam:PassRole and Lambda policy permissions.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

MANAGED_BY = "AussieEcoGuard"
SPECIES = {
    "cat": ["cats", "kitten", "kittens"],
    "dog": ["dogs", "puppy", "puppies"],
    "fox": ["foxes", "red fox", "red foxes"],
    "feral_pig": ["feral pig", "feral pigs", "wild pig", "wild pigs", "wild boar"],
    "cane_toad": ["cane toad", "cane toads"],
    "pig": ["pigs"],
    "toad": ["toads"],
}
COLORS = {"ginger": ["orange"], "black": [], "white": [], "brown": [], "grey": ["gray"], "tabby": [], "golden": []}
INTENTS = {
    "SearchAnimals": ["show me {species}", "find {species}", "show me {count} {species}", "show me {species} from {date}", "show me {count} {species} from {date}", "search for {color} {species}"],
    "FindPet": ["find my {species}", "find my {color} {species}", "I lost my {species}", "I lost a {color} {species}", "find my {species} seen {date}", "find my {color} {species} seen {date}"],
    "HelpIntent": ["help", "what can you do", "how do I search for animals"],
    "FallbackIntent": [],
}


@dataclass(frozen=True)
class Settings:
    region: str
    role_arn: str
    fulfillment_arn: str
    bot_name: str = "ecoguard-assistant"
    alias_name: str = "live"
    locale: str = "en_US"
    bot_id: str | None = None
    wait_seconds: int = 900

    def validate(self):
        role = re.fullmatch(r"arn:(aws(?:-us-gov|-cn)?):iam::(\d{12}):role/(.+)", self.role_arn)
        function = re.fullmatch(r"arn:(aws(?:-us-gov|-cn)?):lambda:([a-z0-9-]+):(\d{12}):function:([A-Za-z0-9_-]+)(?::([A-Za-z0-9_-]+))?", self.fulfillment_arn)
        if not role or not function:
            raise ValueError("Supply a valid existing IAM role ARN and Lambda function/alias ARN.")
        if (role[1], role[2]) != (function[1], function[3]) or function[2] != self.region:
            raise ValueError("The Lex role and fulfillment Lambda must belong to the same AWS account and deployment region.")
        if self.locale != "en_US":
            raise ValueError("This version's utterances and slots are reviewed for en_US only.")
        for name in (self.bot_name, self.alias_name):
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,98}[A-Za-z0-9])?", name):
                raise ValueError("Bot and alias names must be 1–100 alphanumeric/underscore/hyphen characters.")
        if self.bot_id and not re.fullmatch(r"[A-Za-z0-9]{10}", self.bot_id):
            raise ValueError("An existing Lex bot ID must have 10 alphanumeric characters.")
        if not 30 <= self.wait_seconds <= 3600:
            raise ValueError("wait-seconds must be between 30 and 3600.")
        return role[1], role[2], role[3].rsplit("/", 1)[-1]


def plan(settings):
    settings.validate()
    return {
        "mode": "plan", "awsCalls": 0, "region": settings.region,
        "botName": settings.bot_name, "existingBotId": settings.bot_id,
        "roleArn": settings.role_arn, "fulfillmentArn": settings.fulfillment_arn,
        "localeId": settings.locale, "aliasName": settings.alias_name,
        "intents": {name: {"utterances": samples, "slots": ["species (required)", "count (optional)", "color (optional)", "date (optional)"] if name in ("SearchAnimals", "FindPet") else []} for name, samples in INTENTS.items()},
        "steps": ["Read caller identity, existing role trust and Lambda", "Create or reconcile the managed bot's DRAFT locale, slot types and intents", "Build locale and wait for Built", "Publish immutable bot version and wait for Available", "Create/update alias with fulfillment Lambda", "Add an alias-scoped Lambda resource-policy permission and wait for alias Available", "Return ECO_LEX_BOT_ID, ECO_LEX_ALIAS_ID and ECO_LEX_LOCALE for infrastructure configuration"],
        "iamChanges": False,
        "note": "Requires explicit --apply. Does not change Lambda environment, IAM roles/trust, or teaching-account permissions. Lex live acceptance remains a separate authenticated application test.",
    }


class Deployer:
    def __init__(self, settings, session, *, sleep=time.sleep, clock=time.monotonic, progress=None):
        self.cfg = settings
        self.partition, self.account, self.role_name = settings.validate()
        self.lex = session.client("lexv2-models", region_name=settings.region)
        self.iam = session.client("iam")
        self.functions = session.client("lambda", region_name=settings.region)
        self.sts = session.client("sts", region_name=settings.region)
        self.sleep, self.clock = sleep, clock
        self.progress = progress or (lambda message: print(message, file=sys.stderr, flush=True))

    def _all(self, operation, field, **kwargs):
        while True:
            page = getattr(self.lex, operation)(**kwargs)
            yield from page.get(field, [])
            if not page.get("nextToken"):
                return
            kwargs["nextToken"] = page["nextToken"]

    def _wait(self, operation, field, accepted, **kwargs):
        deadline = self.clock()+self.cfg.wait_seconds
        last = None
        while True:
            status = getattr(self.lex, operation)(**kwargs)
            value = status[field]
            if value != last:
                self.progress(f"{operation}: {value}")
                last = value
            if value in accepted:
                return status
            if value in ("Failed", "Deleting"):
                raise RuntimeError(f"Lex {operation} failed: {status.get('failureReasons', [value])}")
            if self.clock() >= deadline:
                raise TimeoutError(f"Lex {operation} did not finish within {self.cfg.wait_seconds}s; rerun with the displayed bot ID to resume.")
            self.sleep(5)

    def _preflight(self):
        if self.sts.get_caller_identity()["Account"] != self.account:
            raise ValueError("The active AWS profile belongs to a different account.")
        role = self.iam.get_role(RoleName=self.role_name)["Role"]
        if role["Arn"] != self.cfg.role_arn:
            raise ValueError("The selected IAM role does not match the supplied ARN.")
        document = role["AssumeRolePolicyDocument"]
        if isinstance(document, str):
            from urllib.parse import unquote
            document = json.loads(unquote(document))
        def includes(value, expected):
            return expected in (value if isinstance(value, list) else [value])
        if not any(s.get("Effect") == "Allow" and includes(s.get("Action"), "sts:AssumeRole") and includes(s.get("Principal", {}).get("Service"), "lexv2.amazonaws.com") for s in document.get("Statement", [])):
            raise ValueError("The existing role does not trust lexv2.amazonaws.com. Ask the account administrator for an approved Lex role; this tool never changes trust.")
        self.functions.get_function(FunctionName=self.cfg.fulfillment_arn)

    def _bot(self):
        bot_id = self.cfg.bot_id
        if not bot_id:
            found = [b for b in self._all("list_bots", "botSummaries", filters=[{"name": "BotName", "values": [self.cfg.bot_name], "operator": "EQ"}], maxResults=100) if b["botName"] == self.cfg.bot_name]
            if len(found) > 1:
                raise ValueError("More than one matching bot exists; specify --bot-id.")
            bot_id = found[0]["botId"] if found else None
        if bot_id:
            details = self.lex.describe_bot(botId=bot_id)
            if details["roleArn"] != self.cfg.role_arn or details["botName"] != self.cfg.bot_name:
                raise ValueError("Existing bot name or role differs; refusing to take over another deployment.")
            tags = self.lex.list_tags_for_resource(resourceARN=f"arn:{self.partition}:lex:{self.cfg.region}:{self.account}:bot/{bot_id}")["tags"]
            if tags.get("ManagedBy") != MANAGED_BY:
                raise ValueError("Existing bot lacks ManagedBy=AussieEcoGuard; refusing to modify an unrelated bot.")
        else:
            bot_id = self.lex.create_bot(botName=self.cfg.bot_name, roleArn=self.cfg.role_arn, dataPrivacy={"childDirected": False}, idleSessionTTLInSeconds=3600, botTags={"ManagedBy": MANAGED_BY, "Project": "FIT3184-A2"})["botId"]
        self.progress(f"Lex bot ID: {bot_id}")
        self._wait("describe_bot", "botStatus", {"Available"}, botId=bot_id)
        return bot_id

    def _slot_type(self, common, name, values):
        found = next((t for t in self._all("list_slot_types", "slotTypeSummaries", **common, maxResults=100) if t["slotTypeName"] == name), None)
        args = {**common, "slotTypeName": name, "slotTypeValues": [{"sampleValue": {"value": value}, **({"synonyms": [{"value": s} for s in synonyms]} if synonyms else {})} for value, synonyms in values.items()], "valueSelectionSetting": {"resolutionStrategy": "TOP_RESOLUTION"}}
        if found:
            return self.lex.update_slot_type(**args, slotTypeId=found["slotTypeId"])["slotTypeId"]
        return self.lex.create_slot_type(**args)["slotTypeId"]

    def _intent(self, common, name, species_type, color_type):
        found = next((i for i in self._all("list_intents", "intentSummaries", **common, maxResults=100) if i["intentName"] == name), None)
        parent = {"parentIntentSignature": "AMAZON.FallbackIntent"} if name == "FallbackIntent" else {}
        base = {**common, "intentName": name, "description": f"Aussie EcoGuard {name}", "fulfillmentCodeHook": {"enabled": True}, "dialogCodeHook": {"enabled": False}, **parent}
        intent_id = found["intentId"] if found else self.lex.create_intent(**base)["intentId"]
        priorities = []
        if name in ("SearchAnimals", "FindPet"):
            slots = {s["slotName"]: s["slotId"] for s in self._all("list_slots", "slotSummaries", **common, intentId=intent_id, maxResults=100)}
            definitions = [("species", species_type, "Which animal species are you looking for?"), ("count", "AMAZON.Number", None), ("color", color_type, None), ("date", "AMAZON.Date", None)]
            for slot_name, slot_type, prompt in definitions:
                setting = {"slotConstraint": "Required" if prompt else "Optional"}
                if prompt:
                    setting["promptSpecification"] = {"maxRetries": 2, "allowInterrupt": True, "messageGroups": [{"message": {"plainTextMessage": {"value": prompt}}}]}
                args = {**common, "intentId": intent_id, "slotName": slot_name, "slotTypeId": slot_type, "valueElicitationSetting": setting}
                response = self.lex.update_slot(**args, slotId=slots[slot_name]) if slot_name in slots else self.lex.create_slot(**args)
                if prompt:
                    priorities.append({"priority": 1, "slotId": response["slotId"]})
        self.lex.update_intent(**base, intentId=intent_id, sampleUtterances=[{"utterance": u} for u in INTENTS[name]], slotPriorities=priorities)

    def _permission(self, bot_id, alias_id):
        source_arn = f"arn:{self.partition}:lex:{self.cfg.region}:{self.account}:bot-alias/{bot_id}/{alias_id}"
        statement_id = f"EcoGuardLex-{bot_id}-{alias_id}"
        args = {"FunctionName": self.cfg.fulfillment_arn, "StatementId": statement_id, "Action": "lambda:InvokeFunction", "Principal": "lexv2.amazonaws.com", "SourceArn": source_arn, "SourceAccount": self.account}
        try:
            self.functions.add_permission(**args)
        except self.functions.exceptions.ResourceConflictException:
            policy = json.loads(self.functions.get_policy(FunctionName=self.cfg.fulfillment_arn)["Policy"])
            old = next((s for s in policy.get("Statement", []) if s.get("Sid") == statement_id), {})
            conditions = old.get("Condition", {})
            if old.get("Effect") != "Allow" or old.get("Action") != "lambda:InvokeFunction" or old.get("Principal", {}).get("Service") != "lexv2.amazonaws.com" or conditions.get("ArnLike", {}).get("AWS:SourceArn") != source_arn or conditions.get("StringEquals", {}).get("AWS:SourceAccount") != self.account:
                raise ValueError("A conflicting Lambda permission exists. Review it with the account administrator; this tool will not replace or broaden it.")

    def apply(self):
        self._preflight()
        bot_id = self._bot()
        common = {"botId": bot_id, "botVersion": "DRAFT", "localeId": self.cfg.locale}
        locales = list(self._all("list_bot_locales", "botLocaleSummaries", botId=bot_id, botVersion="DRAFT", maxResults=100))
        if not any(l["localeId"] == self.cfg.locale for l in locales):
            self.lex.create_bot_locale(**common, nluIntentConfidenceThreshold=0.4)
        self._wait("describe_bot_locale", "botLocaleStatus", {"NotBuilt", "Built", "ReadyExpressTesting"}, **common)
        species = self._slot_type(common, "EcoGuardSpecies", SPECIES)
        colors = self._slot_type(common, "EcoGuardColor", COLORS)
        for name in INTENTS:
            self._intent(common, name, species, colors)
        self.lex.build_bot_locale(**common)
        self._wait("describe_bot_locale", "botLocaleStatus", {"Built"}, **common)
        version = self.lex.create_bot_version(botId=bot_id, botVersionLocaleSpecification={self.cfg.locale: {"sourceBotVersion": "DRAFT"}}, description="Reviewed EcoGuard species/count/color/date intents")["botVersion"]
        self._wait("describe_bot_version", "botStatus", {"Available"}, botId=bot_id, botVersion=version)
        aliases = list(self._all("list_bot_aliases", "botAliasSummaries", botId=bot_id, maxResults=100))
        existing = next((a for a in aliases if a["botAliasName"] == self.cfg.alias_name), None)
        args = {"botId": bot_id, "botAliasName": self.cfg.alias_name, "botVersion": version, "botAliasLocaleSettings": {self.cfg.locale: {"enabled": True, "codeHookSpecification": {"lambdaCodeHook": {"lambdaARN": self.cfg.fulfillment_arn, "codeHookInterfaceVersion": "1.0"}}}}, "sentimentAnalysisSettings": {"detectSentiment": False}}
        alias = self.lex.update_bot_alias(**args, botAliasId=existing["botAliasId"]) if existing else self.lex.create_bot_alias(**args, tags={"ManagedBy": MANAGED_BY})
        alias_id = alias["botAliasId"]
        self._permission(bot_id, alias_id)
        self._wait("describe_bot_alias", "botAliasStatus", {"Available"}, botId=bot_id, botAliasId=alias_id)
        return {"mode": "applied", "botId": bot_id, "botVersion": version, "botAliasId": alias_id, "region": self.cfg.region, "environment": {"ECO_LEX_BOT_ID": bot_id, "ECO_LEX_ALIAS_ID": alias_id, "ECO_LEX_LOCALE": self.cfg.locale}, "liveAcceptance": "pending authenticated application session and RecognizeText verification"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--fulfillment-arn", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--bot-name", default="ecoguard-assistant")
    parser.add_argument("--alias-name", default="live")
    parser.add_argument("--bot-id")
    parser.add_argument("--locale", default="en_US")
    parser.add_argument("--profile")
    parser.add_argument("--wait-seconds", type=int, default=900)
    parser.add_argument("--output", type=Path, help="Optional JSON result file; contains resource IDs, never credentials")
    parser.add_argument("--apply", action="store_true", help="Create/update Lex and its scoped Lambda invocation permission")
    args = parser.parse_args(argv)
    cfg = Settings(args.region, args.role_arn, args.fulfillment_arn, args.bot_name, args.alias_name, args.locale, args.bot_id, args.wait_seconds)
    try:
        result = plan(cfg)
        if args.apply:
            import boto3
            result = Deployer(cfg, boto3.Session(profile_name=args.profile, region_name=args.region)).apply()
        encoded = json.dumps(result, indent=2)
        if args.output:
            args.output.write_text(encoded+"\n", encoding="utf-8")
        print(encoded)
        return 0
    except Exception as error:
        code = getattr(error, "response", {}).get("Error", {}).get("Code")
        print(f"Lex deployment stopped: {code or type(error).__name__}: {error if not code else 'AWS denied or could not complete the operation; no IAM workaround was attempted.'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
