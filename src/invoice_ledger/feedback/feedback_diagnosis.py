"""Future feedback diagnosis boundary. Stage one does not implement Agent diagnosis."""

from __future__ import annotations


def diagnose_feedback(feedback_ref: str) -> dict[str, str]:
    return {
        "status": "failed",
        "capability": "feedback_diagnosis",
        "stage": "future",
        "feedback_ref": feedback_ref,
        "message": "Agent 自动反馈诊断闭环属于后续阶段，当前阶段不支持。",
    }
