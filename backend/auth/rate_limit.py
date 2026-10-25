from collections import deque
from datetime import timedelta
from math import ceil
from threading import Lock

from backend.domain.clock import Clock

# API 9, NF-06: prototype limits (the final numbers are open, ARCHITECTURE 12). Counts live in memory, so they are
# per process and start again at a restart; one running instance is the prototype's set-up.
SIGN_IN_FAILURES = 5
SIGN_IN_WINDOW = timedelta(minutes=15)
REQUESTS_PER_SESSION = 120
REQUEST_WINDOW = timedelta(minutes=1)
# Every sign-in or sign-up attempt costs a memory-hungry password hash, so one address is also limited on its own,
# whatever emails it tries. Only failed sign-ins count, so people sharing an address (a store's kiosks) are not
# blocked by their own successful sign-ins. Prototype values, open like the others.
SIGN_IN_FAILURES_PER_ADDRESS = 30
SIGN_IN_ADDRESS_WINDOW = timedelta(minutes=15)
SIGN_UPS_PER_ADDRESS = 10
SIGN_UP_WINDOW = timedelta(hours=1)

_SWEEP_ABOVE = 10_000  # keys held before expired ones are cleared out, so random emails cannot grow memory forever


class RateLimiter:
    """Allows `limit` recorded events per key within a sliding window."""

    def __init__(self, limit: int, window: timedelta, clock: Clock):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._events: dict[object, deque] = {}
        self._lock = Lock()

    def seconds_until_allowed(self, key) -> int:
        """0 when the key may go ahead, otherwise whole seconds until its oldest event leaves the window."""
        with self._lock:
            events = self._events.get(key)
            if events is None:
                return 0
            self._drop_old(events)
            if len(events) < self.limit:
                return 0
            return max(1, ceil((events[0] + self.window - self.clock.now()).total_seconds()))

    def record(self, key):
        with self._lock:
            events = self._events.setdefault(key, deque())
            self._drop_old(events)
            events.append(self.clock.now())
            if len(self._events) > _SWEEP_ABOVE:
                self._sweep()

    def forget(self, key):
        with self._lock:
            self._events.pop(key, None)

    def _drop_old(self, events: deque):
        cutoff = self.clock.now() - self.window
        while events and events[0] <= cutoff:
            events.popleft()

    def _sweep(self):
        for key, events in list(self._events.items()):
            self._drop_old(events)
            if not events:
                del self._events[key]
