"""The audit hash chain detects content and ordering changes."""

from __future__ import annotations

from trader.core.audit import audit_hash, verify_audit_chain


def test_audit_chain_verifies_in_order() -> None:
    """Valid entries link every row to the previous row hash."""
    first_payload = '{"event":"started"}'
    first_hash = audit_hash("", first_payload)
    second_payload = '{"event":"stopped"}'
    entries = [
        {"prev_hash": "", "payload": first_payload, "hash": first_hash},
        {
            "prev_hash": first_hash,
            "payload": second_payload,
            "hash": audit_hash(first_hash, second_payload),
        },
    ]
    assert verify_audit_chain(entries)


def test_audit_chain_rejects_tampered_payload() -> None:
    """Changing a stored event body invalidates its digest."""
    entry = {"prev_hash": "", "payload": '{"event":"started"}', "hash": "wrong"}
    assert not verify_audit_chain([entry])
