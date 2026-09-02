"""Budget safety limits with pre-call blocking.

Four limits, all in EUR, all strictly positive and never removable:
- ``per_call_eur``: worst-case cost of a single call.
- ``project_eur``: cumulative cost of one editorial project.
- ``monthly_hard_eur``: cumulative cost in the current calendar month (UTC).
- ``warn_eur``: monthly threshold that raises a warning (never blocks).

Defaults come from settings; administrators override them in the
``budget_limits`` table (API/CLI). Every check happens *before* spend with a
worst-case estimate, and every actual spend is recorded in ``cost_entries``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mpt2.errors import MPT2Error
from mpt2.models import BudgetLimit, CostEntry, utcnow
from mpt2.settings import Settings

LIMIT_KEYS = ("warn_eur", "monthly_hard_eur", "project_eur", "per_call_eur")


class BudgetExceededError(MPT2Error):
    code = "budget_exceeded"


@dataclass(frozen=True)
class BudgetSnapshot:
    warn_eur: float
    monthly_hard_eur: float
    project_eur: float
    per_call_eur: float
    month_spent_eur: float
    project_spent_eur: float
    warning: bool

    def as_dict(self) -> dict[str, float | bool]:
        return {
            "warn_eur": self.warn_eur,
            "monthly_hard_eur": self.monthly_hard_eur,
            "project_eur": self.project_eur,
            "per_call_eur": self.per_call_eur,
            "month_spent_eur": round(self.month_spent_eur, 4),
            "project_spent_eur": round(self.project_spent_eur, 4),
            "warning": self.warning,
        }


def month_start(now: datetime | None = None) -> datetime:
    moment = now or utcnow()
    return moment.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    )


class BudgetGuard:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._warned_month: str | None = None

    # ------------------------------------------------------------ limits
    def limits(self, session: Session) -> dict[str, float]:
        values = {
            "warn_eur": self.settings.budget_warn_eur,
            "monthly_hard_eur": self.settings.budget_monthly_hard_eur,
            "project_eur": self.settings.budget_project_eur,
            "per_call_eur": self.settings.budget_per_call_eur,
        }
        for row in session.scalars(select(BudgetLimit)).all():
            if row.key in values:
                values[row.key] = float(row.value_eur)
        return values

    def set_limits(
        self,
        session: Session,
        updates: dict[str, float],
        *,
        actor: str,
        note: str | None = None,
    ) -> dict[str, float]:
        """Admin change. Limits must stay positive and consistent; they can never be removed."""
        current = self.limits(session)
        merged = dict(current)
        for key, value in updates.items():
            if key not in LIMIT_KEYS:
                raise ValueError(f"unknown budget limit {key!r}")
            if value is None or float(value) <= 0:
                raise ValueError(
                    f"{key} must be a positive amount; safety limits cannot be removed"
                )
            merged[key] = float(value)
        if merged["project_eur"] > merged["monthly_hard_eur"]:
            raise ValueError("project_eur cannot exceed monthly_hard_eur")
        if merged["per_call_eur"] > merged["project_eur"]:
            raise ValueError("per_call_eur cannot exceed project_eur")
        for key, value in updates.items():
            row = session.get(BudgetLimit, key)
            if row is None:
                row = BudgetLimit(key=key)
                session.add(row)
            row.value_eur = float(value)
            row.updated_by = actor
            row.note = note
            row.updated_at = utcnow()
        session.flush()
        return merged

    # ------------------------------------------------------------- spend
    def month_spent(self, session: Session, now: datetime | None = None) -> float:
        total = session.scalar(
            select(func.coalesce(func.sum(CostEntry.est_cost_eur), 0.0)).where(
                CostEntry.created_at >= month_start(now)
            )
        )
        return float(total or 0.0)

    def project_spent(self, session: Session, project_id: str | None) -> float:
        if not project_id:
            return 0.0
        total = session.scalar(
            select(func.coalesce(func.sum(CostEntry.est_cost_eur), 0.0)).where(
                CostEntry.project_id == project_id
            )
        )
        return float(total or 0.0)

    def snapshot(
        self, session: Session, project_id: str | None = None
    ) -> BudgetSnapshot:
        limits = self.limits(session)
        month = self.month_spent(session)
        project = self.project_spent(session, project_id)
        return BudgetSnapshot(
            warn_eur=limits["warn_eur"],
            monthly_hard_eur=limits["monthly_hard_eur"],
            project_eur=limits["project_eur"],
            per_call_eur=limits["per_call_eur"],
            month_spent_eur=month,
            project_spent_eur=project,
            warning=month >= limits["warn_eur"],
        )

    # ------------------------------------------------------------- check
    def check(
        self,
        session: Session,
        *,
        estimated_eur: float,
        project_id: str | None,
        what: str,
    ) -> BudgetSnapshot:
        """Raise ``BudgetExceededError`` if ``estimated_eur`` would break a hard limit."""
        snap = self.snapshot(session, project_id)
        if estimated_eur > snap.per_call_eur:
            raise BudgetExceededError(
                f"{what}: estimated {estimated_eur:.4f} EUR exceeds per-call limit {snap.per_call_eur:.2f} EUR",
                module=__name__,
            )
        if project_id and snap.project_spent_eur + estimated_eur > snap.project_eur:
            raise BudgetExceededError(
                f"{what}: project spent {snap.project_spent_eur:.4f} EUR + {estimated_eur:.4f} EUR would exceed "
                f"project limit {snap.project_eur:.2f} EUR",
                module=__name__,
            )
        if snap.month_spent_eur + estimated_eur > snap.monthly_hard_eur:
            raise BudgetExceededError(
                f"{what}: month spent {snap.month_spent_eur:.4f} EUR + {estimated_eur:.4f} EUR would exceed "
                f"monthly hard limit {snap.monthly_hard_eur:.2f} EUR",
                module=__name__,
            )
        if snap.warning:
            key = month_start().strftime("%Y-%m")
            if self._warned_month != key:
                self._warned_month = key
                logger.warning(
                    f"budget warning: {snap.month_spent_eur:.2f} EUR spent this month, "
                    f"threshold {snap.warn_eur:.2f} EUR"
                )
        return snap
