from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

# the time between a buyer's check and its write; without the lock the other buyer's check would pass inside it
RACE_WINDOW_SECONDS = 0.3


def count_race_winners(attempt):
    """Run `attempt` in two threads released at the same moment; return how many succeeded."""
    barrier = Barrier(2)

    def contender(_):
        barrier.wait()
        return attempt()

    with ThreadPoolExecutor(max_workers=2) as pool:
        return sum(pool.map(contender, range(2)))
