from __future__ import annotations
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from ecoguard_domain import Principal, DomainError
from ecoguard_domain.rules import normalize_tag
from .repository import Write


def _slot(slots, name):
    value = (slots.get(name) or {}).get("value", {})
    resolved = value.get("resolvedValues") or []
    return resolved[0] if resolved else value.get("interpretedValue")


def _reply(intent, message, *, state="Fulfilled", elicit=None):
    action = {"type": "ElicitSlot", "slotToElicit": elicit} if elicit else {"type": "Close"}
    return {"sessionState": {"dialogAction": action, "intent": {**intent, "state": state}}, "messages": [{"contentType": "PlainText", "content": message}]}


def fulfill(event, service):
    session_id = event.get("sessionId", "")
    intent = event.get("sessionState", {}).get("intent", {"name": "FallbackIntent", "slots": {}})
    try:
        binding = service.operation(f"CHAT#{session_id}")
        if binding.get("type") != "CHAT" or binding.get("expiresAt", 0) < time.time():
            raise DomainError("SESSION_EXPIRED", "Please start a new chat session.")
        principal = Principal(binding["ownerSub"])
        name, slots = intent["name"], intent.get("slots", {})
        if name not in ("SearchAnimals", "FindPet"):
            return _reply(intent, "Ask for animal sightings, for example 'show me two foxes' or 'find a ginger cat'.")
        species = _slot(slots, "species") or _slot(slots, "Species")
        if not species:
            return _reply(intent, "Which animal species are you looking for?", state="InProgress", elicit="species")
        conditions = {normalize_tag(species): int(_slot(slots, "count") or 1)}
        color = _slot(slots, "color")
        if color:
            conditions[normalize_tag(color)] = 1
        query = {"minimumCounts": conditions}
        date = _slot(slots, "date")
        if date:
            zone = ZoneInfo("Australia/Sydney")
            local = datetime.now(zone)
            if date.lower() == "yesterday":
                date = (local-timedelta(days=1)).date().isoformat()
            start = datetime.fromisoformat(date).replace(tzinfo=zone)
            query.update({"from": start.astimezone(timezone.utc).isoformat(), "to": (start+timedelta(days=1)).astimezone(timezone.utc).isoformat()})
        elif name == "FindPet":
            query["from"] = (datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
        items = service.search(principal, query, raw=True, public_only=name == "FindPet")[:10]
        service.repo.transact([Write("operations", dict(binding, resultIds=[m["mediaId"] for m in items]), binding["_rev"])])
        if not items:
            return _reply(intent, "No accessible sightings matched those conditions. Try a wider date range or different tags.")
        links = "\n".join(f"{service.config.web_url.rstrip('/')}/media/{m['mediaId']}" for m in items)
        return _reply(intent, f"Found {len(items)} matching sightings. These are possible matches, not confirmed pet identities.\n{links}")
    except (DomainError, ValueError) as error:
        message = error.message if isinstance(error, DomainError) else "Please use a valid animal count and date."
        return _reply(intent, message, state="Failed")
