"""A single, simple worker loop over the durable queue.

- recovers stale locks left by a crashed process on start-up and periodically
- runs pending jobs one at a time (stages are CPU-light but API-bound)
- exits cleanly on SIGINT/SIGTERM; a restart resumes where it stopped
"""

from __future__ import annotations

import signal
import time
from typing import Callable

from loguru import logger

from mpt2.db import session_scope
from mpt2.jobs import JobQueue


class Worker:
    def __init__(
        self,
        queue: JobQueue,
        session_factory: Callable,
        *,
        poll_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.queue = queue
        self._factory = session_factory
        self.poll_seconds = poll_seconds
        self._sleep = sleep
        self._stop = False

    def stop(self, *_args) -> None:
        self._stop = True

    def recover(self) -> int:
        with session_scope(self._factory) as session:
            return self.queue.recover_stale(session)

    def run_until_idle(self, *, max_jobs: int = 500) -> int:
        recovered = self.recover()
        if recovered:
            logger.warning(f"worker recovered {recovered} stale job(s)")
        return self.queue.run_pending(max_jobs=max_jobs)

    def run_forever(self, *, max_iterations: int | None = None) -> int:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        total = 0
        iterations = 0
        self.recover()
        while not self._stop:
            ran = self.queue.run_pending(max_jobs=50)
            total += ran
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            if ran == 0:
                self._sleep(self.poll_seconds)
                if iterations % 30 == 0:
                    self.recover()
        logger.info(f"worker stopped after {total} job(s)")
        return total
