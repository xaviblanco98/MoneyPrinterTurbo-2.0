"""MoneyPrinterTurbo 2.0 — Autonomous Media Engine.

``mpt2`` is a separate, additive package that lives next to the upstream
MoneyPrinterTurbo code (``app/``). It never modifies upstream behaviour; it
reuses upstream services through thin adapters where that makes sense.

Milestone H1 provides only the persistent, verifiable foundations: settings
from environment variables, a SQLite database with versioned migrations, the
data models, an explicit project state machine, a durable job queue with
controlled retries, provider contracts and a minimal HTTP API.
"""

__version__ = "2.0.0-h1"
