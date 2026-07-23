"""Structured errors for the invoice draft ledger pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class InvoiceLedgerError(Exception):
    message: str
    layer: str = "unknown"
    suggestion: str | None = None
    details: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "message": self.message,
            "layer": self.layer,
            "suggestion": self.suggestion,
            "details": self.details,
        }
