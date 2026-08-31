"""The monthly spending ceiling, shared by every paid feature.

This lived in `features/ai.py` while Gemini was the only thing that cost money.
A second paid provider cannot import it from there — feature modules never
import each other — and a budget with two copies of the ceiling is not a budget.
So it moved here, and `features/ai.py` re-exports the same names.

**Costs are in paise, not rupees**, for the reason given in `features/ai.py`:
currency in floating point drifts, and this total gets compared against a
provider's console, where a few paise of disagreement would undermine confidence
in the whole figure.
"""

from __future__ import annotations

from typing import Any

from backend import db

MONTHLY_BUDGET_PAISE = 200_000  # ₹2,000


class OverBudget(RuntimeError):
    """The month's budget is used up and no override was given.

    A plain `RuntimeError` rather than a provider's error type, because the
    ceiling is the shop's, not any one provider's. Each feature catches it
    explicitly at its own call site.
    """


def budget_status(month: str | None = None) -> dict[str, Any]:
    summary = db.spend_summary(month)
    spent = int(summary["total_paise"])
    return summary | {
        "budget_paise": MONTHLY_BUDGET_PAISE,
        "spent_rupees": round(spent / 100, 2),
        "budget_rupees": round(MONTHLY_BUDGET_PAISE / 100, 2),
        "fraction_used": round(min(spent / MONTHLY_BUDGET_PAISE, 1.0), 4),
        "over_budget": spent > MONTHLY_BUDGET_PAISE,
        # An estimate, and the UI must say so — the provider's console is truth.
        "is_estimate": True,
    }


def ensure_within(override: bool = False, provider: str = "AI") -> None:
    """Refuse a paid call once the month's budget is gone.

    `override` is the operator's explicit "spend anyway" — this is their shop and
    their money, so the ceiling must be passable. It just may not be passed by
    accident.
    """
    if override:
        return
    status = budget_status()
    if not status["over_budget"]:
        return
    raise OverBudget(
        f"This month's AI spending has reached ₹{status['spent_rupees']:.2f}, past "
        f"the ₹{status['budget_rupees']:.0f} budget. Nothing was sent and nothing "
        f"was charged. Check {provider}'s console for the real figure — this total "
        f"is an estimate — then tick “spend past the budget” to continue."
    )


__all__ = ["MONTHLY_BUDGET_PAISE", "OverBudget", "budget_status", "ensure_within"]
