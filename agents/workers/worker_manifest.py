from dataclasses import dataclass
from typing import Literal

# ARCHITECTURE 5.5, AGENTS 4: what a worker declares about itself. The agent registry (Phase 12) reads these to
# build the orchestrator's tools and to keep a disabled worker's tools out of the prompt (EX-01).

# read: changes nothing. propose: prepares an action that needs a confirm button. write: runs only inside the
# domain core's transaction (backend/domain/transaction.py).
Effect = Literal["read", "propose", "write"]


@dataclass(frozen=True)
class Operation:
    name: str
    effect: Effect
    timeout_ms: int


@dataclass(frozen=True)
class WorkerManifest:
    name: str
    description: str
    operations: tuple[Operation, ...]
    requires: tuple[str, ...] = ()  # workers this one calls
    enabled: bool = True
