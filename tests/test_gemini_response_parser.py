import json

from app.brain.orchestrator import BrainOrchestrator


def test_extract_json_from_prose_wrapped_response() -> None:
    payload = {"action": "WAIT", "summary": "mixed signals"}
    text = "Here is the structured result:\n" + json.dumps(payload) + "\nEnd."
    assert BrainOrchestrator._extract_json_object(text) == payload


def test_extract_json_from_fenced_response() -> None:
    payload = {"action": "WAIT", "summary": "no trade"}
    text = "```json\n" + json.dumps(payload) + "\n```"
    assert BrainOrchestrator._extract_json_object(text) == payload


def test_non_structured_response_remains_safe_wait() -> None:
    decision = BrainOrchestrator._parse_decision({"ok": True, "text": "I cannot provide JSON right now."})
    assert decision["action"] == "WAIT"
    assert decision["scoring"]["approval_score"] == 0
