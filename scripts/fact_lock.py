"""Validate drafts against an explicit, human-reviewed story fact ledger."""
from __future__ import annotations

import json
from pathlib import Path


def _object(value, where, allowed, required):
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown:
        raise ValueError(f"{where} has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"{where} is missing fields: {', '.join(sorted(missing))}")


def _text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a nonempty string")


def _phrases(value, where, required=True):
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        expectation = "a nonempty list" if required else "a list"
        raise ValueError(f"{where} must be {expectation} of nonempty strings")
    for index, phrase in enumerate(value):
        _text(phrase, f"{where}[{index}]")
    return value


def validate_fact_lock(data):
    """Validate the deliberately small JSON format used by the checker."""
    _object(data, "fact-lock", ("version", "title", "facts", "events", "order"), ("version",))
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("fact-lock.version must be 1")
    if "title" in data:
        _text(data["title"], "fact-lock.title")

    facts = data.get("facts", [])
    events = data.get("events", [])
    orders = data.get("order", [])
    if not isinstance(facts, list) or not isinstance(events, list) or not isinstance(orders, list):
        raise ValueError("fact-lock.facts, events, and order must be lists")
    if not facts and not events:
        raise ValueError("fact-lock must define at least one fact or event")

    ids = set()
    for index, fact in enumerate(facts):
        where = f"fact-lock.facts[{index}]"
        _object(fact, where, ("id", "claim", "match_any", "conflict_any"),
                ("id", "claim", "match_any"))
        _text(fact["id"], f"{where}.id")
        _text(fact["claim"], f"{where}.claim")
        _phrases(fact["match_any"], f"{where}.match_any")
        _phrases(fact.get("conflict_any"), f"{where}.conflict_any", required=False)
        if fact["id"] in ids:
            raise ValueError(f"fact-lock has duplicate id: {fact['id']}")
        ids.add(fact["id"])

    event_ids = set()
    for index, event in enumerate(events):
        where = f"fact-lock.events[{index}]"
        _object(event, where, ("id", "claim", "match_any"), ("id", "claim", "match_any"))
        _text(event["id"], f"{where}.id")
        _text(event["claim"], f"{where}.claim")
        _phrases(event["match_any"], f"{where}.match_any")
        if event["id"] in ids:
            raise ValueError(f"fact-lock has duplicate id: {event['id']}")
        ids.add(event["id"])
        event_ids.add(event["id"])

    seen_orders = set()
    for index, item in enumerate(orders):
        where = f"fact-lock.order[{index}]"
        _object(item, where, ("before", "after"), ("before", "after"))
        _text(item["before"], f"{where}.before")
        _text(item["after"], f"{where}.after")
        if item["before"] not in event_ids or item["after"] not in event_ids:
            raise ValueError(f"{where} must reference event ids")
        if item["before"] == item["after"]:
            raise ValueError(f"{where} must reference two different events")
        pair = (item["before"], item["after"])
        if pair in seen_orders:
            raise ValueError(f"{where} duplicates an event-order check")
        seen_orders.add(pair)
    return data


def read_fact_lock(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read fact-lock JSON {path}: {exc}") from exc
    return validate_fact_lock(data)


def _hits(text, phrases):
    hits = []
    for phrase in phrases:
        position = text.find(phrase)
        if position >= 0:
            hits.append({
                "phrase": phrase,
                "position": position,
                "line": text.count("\n", 0, position) + 1,
            })
    return sorted(hits, key=lambda hit: (hit["position"], hit["phrase"]))


def check_fact_lock(text, data):
    """Check explicit phrase assertions and first-mention event order."""
    validate_fact_lock(data)
    blocking = []
    checked_facts = []
    for fact in data.get("facts", []):
        matched = _hits(text, fact["match_any"])
        conflicts = _hits(text, fact.get("conflict_any", []))
        status = "pass"
        if not matched:
            status = "missing"
            blocking.append(
                f"fact-lock-missing ({fact['id']}): {fact['claim']} "
                f"(expected one of {fact['match_any']})"
            )
        if conflicts:
            status = "conflict"
            blocking.append(
                f"fact-lock-conflict ({fact['id']}): {fact['claim']} "
                f"(found {', '.join(hit['phrase'] for hit in conflicts)})"
            )
        checked_facts.append({
            "id": fact["id"],
            "claim": fact["claim"],
            "status": status,
            "matched": matched,
            "conflicts": conflicts,
        })

    event_results = {}
    checked_events = []
    for event in data.get("events", []):
        matched = _hits(text, event["match_any"])
        status = "pass" if matched else "missing"
        if not matched:
            blocking.append(
                f"fact-lock-event-missing ({event['id']}): {event['claim']} "
                f"(expected one of {event['match_any']})"
            )
        event_results[event["id"]] = matched
        checked_events.append({
            "id": event["id"],
            "claim": event["claim"],
            "status": status,
            "matched": matched,
        })

    checked_order = []
    for item in data.get("order", []):
        before_hits = event_results[item["before"]]
        after_hits = event_results[item["after"]]
        if not before_hits or not after_hits:
            status = "skipped_missing_event"
        elif before_hits[0]["position"] < after_hits[0]["position"]:
            status = "pass"
        else:
            status = "violated"
            blocking.append(
                f"fact-lock-order ({item['before']} before {item['after']}): "
                "the first matching event markers appear in the opposite order"
            )
        checked_order.append({**item, "status": status})

    return {
        "status": "FAIL" if blocking else "PASS",
        "title": data.get("title"),
        "facts": checked_facts,
        "events": checked_events,
        "order": checked_order,
        "blocking": blocking,
    }
