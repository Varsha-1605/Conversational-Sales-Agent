from agents.workers.worker_manifest import Operation, WorkerManifest

QUOTE_TIMEOUT_MS = 2000

LOYALTY_OFFERS_MANIFEST = WorkerManifest(
    name="loyalty_offers",
    description="The only source of prices, discounts, coupons, points and savings.",
    operations=(Operation("quote", "read", QUOTE_TIMEOUT_MS),),
    requires=("inventory",),  # a line whose stock is not confirmed cannot be quoted
)
