"""Pending Schema bootstrap boundary."""

from __future__ import annotations


def bootstrap_schema(evidence_ref: str) -> dict[str, object]:
    return {
        "status": "pending_confirmation",
        "capability": "schema_bootstrap",
        "promotion": "manual_confirmation_required",
        "active_schema_written": False,
        "evidence_ref": evidence_ref,
        "draft": {
            "schema_id": None,
            "status": "pending",
            "evidence_ref": evidence_ref,
            "suggested_fields": [],
            "review_notes": [
                "新票种只能生成待确认草案。",
                "用户确认前不得写入 active schema。",
            ],
        },
        "message": "已生成新票种待确认草案；用户确认前不会写入 active schema。",
    }
