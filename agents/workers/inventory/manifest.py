from agents.workers.worker_manifest import Operation, WorkerManifest

READ_TIMEOUT_MS = 1000
WRITE_TIMEOUT_MS = 2000

INVENTORY_MANIFEST = WorkerManifest(
    name="inventory",
    description="Stock and availability at every location, and the holds that keep stock from being sold twice.",
    operations=(
        Operation("availability", "read", READ_TIMEOUT_MS),
        Operation("fulfillment_options", "read", READ_TIMEOUT_MS),
        Operation("create_hold", "write", WRITE_TIMEOUT_MS),
        Operation("commit_hold", "write", WRITE_TIMEOUT_MS),
        Operation("release_hold", "write", WRITE_TIMEOUT_MS),
    ),
)
