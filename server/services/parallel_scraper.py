"""
Parallel scraping orchestrator.

Manages a pool of GSAScrapingAutomation worker threads, each with its own
Chrome WebDriver, sharing a global rate limiter and cooperative stop event.
"""
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.scraping_service import GSAScrapingAutomation
from services.rate_limiter import TokenBucketRateLimiter
from settings import (
    EXCEL_FILE_PATH,
    SCRAPE_DELAY_SECONDS,
    SCRAPE_MAX_REQUESTS_PER_MINUTE,
    SCRAPE_MAX_WORKERS,
    SCRAPE_NUM_WORKERS,
    SCRAPE_PROXIES,
    SCRAPE_WORKER_MAX_RETRIES,
)

logger = logging.getLogger(__name__)


def resolve_num_workers(requested: int = 0) -> int:
    """Determine how many workers to use.

    *requested* == 0 means auto-detect based on CPU count.
    """
    if requested > 0:
        return min(requested, SCRAPE_MAX_WORKERS)

    if SCRAPE_NUM_WORKERS > 0:
        return min(SCRAPE_NUM_WORKERS, SCRAPE_MAX_WORKERS)

    cpu = os.cpu_count() or 2
    auto = min(3, max(1, cpu // 2))
    return min(auto, SCRAPE_MAX_WORKERS)


class ProgressTracker:
    """Thread-safe progress aggregator for parallel scraping."""

    def __init__(
        self,
        total: int,
        num_workers: int,
        stop_event: threading.Event,
        stop_after: int = 0,
    ):
        self._lock = threading.Lock()
        self.total = total
        self.num_workers = num_workers
        self.completed = 0
        self.successful = 0
        self.failed = 0
        self.start_time = time.time()
        self._stop_event = stop_event
        self._stop_after = stop_after
        self._worker_status: dict[int, dict] = {
            i: {"completed": 0, "current_part": "", "status": "starting"}
            for i in range(num_workers)
        }

    def record(self, worker_id: int, part_number: str, success: bool):
        with self._lock:
            self.completed += 1
            if success:
                self.successful += 1
            else:
                self.failed += 1
            ws = self._worker_status.get(worker_id, {})
            ws["completed"] = ws.get("completed", 0) + 1
            ws["current_part"] = part_number
            if self._stop_after > 0 and self.completed >= self._stop_after:
                self._stop_event.set()

    def mark_worker(self, worker_id: int, status: str):
        with self._lock:
            if worker_id in self._worker_status:
                self._worker_status[worker_id]["status"] = status

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = time.time() - self.start_time
            avg = elapsed / self.completed if self.completed > 0 else 0
            remaining = (self.total - self.completed) * avg if avg > 0 else 0
            active = sum(1 for w in self._worker_status.values() if w["status"] == "running")
            return {
                "total": self.total,
                "completed": self.completed,
                "successful": self.successful,
                "failed": self.failed,
                "stop_after": self._stop_after,
                "active_workers": active,
                "num_workers": self.num_workers,
                "elapsed_seconds": round(elapsed, 1),
                "avg_seconds_per_row": round(avg, 1),
                "estimated_remaining_seconds": round(remaining, 1),
                "workers": [
                    {"id": wid, **info}
                    for wid, info in sorted(self._worker_status.items())
                ],
            }


class ParallelScrapingOrchestrator:
    """Coordinates multiple GSAScrapingAutomation workers."""

    def __init__(self, num_workers: int = 0, sort_order: str = "low_to_high", stop_after: int = 0):
        self.proxies = list(SCRAPE_PROXIES)  # copy
        self.stop_event = threading.Event()
        self.tracker: ProgressTracker | None = None
        self._executor: ThreadPoolExecutor | None = None
        self.sort_order = sort_order  # passed to InternalLinkScraper
        self.stop_after = stop_after  # 0 = no limit

        if self.proxies:
            # With proxies: each worker has its own IP → no global rate limiter needed.
            # Auto-scale workers to match available proxies if not explicitly set.
            if num_workers > 0:
                self.num_workers = min(num_workers, len(self.proxies), SCRAPE_MAX_WORKERS)
            else:
                self.num_workers = min(len(self.proxies), SCRAPE_MAX_WORKERS)
            self.rate_limiter = None  # each worker does its own per-IP delay
            logger.info(
                f"Orchestrator initialized: {self.num_workers} workers with "
                f"{len(self.proxies)} proxies (each IP rate-limited independently at "
                f"{SCRAPE_DELAY_SECONDS}s/request)"
            )
        else:
            # No proxies: single IP, global rate limiter
            self.num_workers = resolve_num_workers(num_workers)
            self.rate_limiter = TokenBucketRateLimiter(SCRAPE_MAX_REQUESTS_PER_MINUTE)
            logger.info(
                f"Orchestrator initialized: {self.num_workers} workers, "
                f"single IP, {SCRAPE_MAX_REQUESTS_PER_MINUTE} max req/min"
            )

    def stop(self):
        """Signal all workers to stop."""
        logger.info("Orchestrator: broadcasting stop to all workers")
        self.stop_event.set()
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def progress_snapshot(self) -> dict | None:
        if self.tracker:
            return self.tracker.snapshot()
        return None

    # ── Run modes ─────────────────────────────────────────────────────

    def _load_data(self):
        """Load data from imported_parts DB (preferred) or Excel fallback."""
        automation = GSAScrapingAutomation(EXCEL_FILE_PATH)
        df, column_mapping = automation.read_data()
        return df, column_mapping

    def run_test(self, item_limit: int):
        df, column_mapping = self._load_data()
        if df is None:
            return False
        indices = list(df.head(item_limit).index)
        return self._dispatch(indices, df, column_mapping)

    def run_full(self):
        df, column_mapping = self._load_data()
        if df is None:
            return False
        return self._dispatch(list(df.index), df, column_mapping)

    def run_missing(self):
        df, column_mapping = self._load_data()
        if df is None:
            return False
        automation = GSAScrapingAutomation(EXCEL_FILE_PATH)
        indices = automation.identify_missing_rows(df)
        if not indices:
            logger.info("All rows already scraped!")
            return True
        return self._dispatch(indices, df, column_mapping)

    def run_custom_range(self, start_row: int, end_row: int):
        df, column_mapping = self._load_data()
        if df is None:
            return False
        start_idx = max(0, start_row - 1)
        end_idx = min(len(df) - 1, end_row - 1)
        if start_idx > end_idx:
            logger.error(f"Invalid range: {start_row}-{end_row}")
            return False
        return self._dispatch(list(range(start_idx, end_idx + 1)), df, column_mapping)

    # ── Internal ──────────────────────────────────────────────────────

    def _dispatch(self, indices: list, df, column_mapping: dict) -> bool:
        """Partition work and run workers in parallel threads."""
        total = len(indices)
        # For very small jobs, use fewer workers than configured
        actual_workers = min(self.num_workers, total)
        if actual_workers < 1:
            actual_workers = 1

        self.tracker = ProgressTracker(total, actual_workers, self.stop_event, self.stop_after)

        # Interleaved partitioning for even distribution
        chunks = [indices[i::actual_workers] for i in range(actual_workers)]

        logger.info(
            f"Dispatching {total} rows across {actual_workers} workers "
            f"(chunks: {[len(c) for c in chunks]})"
        )

        self._executor = ThreadPoolExecutor(max_workers=actual_workers)
        futures = {}
        for worker_id, chunk in enumerate(chunks):
            future = self._executor.submit(
                self._run_worker, worker_id, chunk, df, column_mapping
            )
            futures[future] = worker_id

        # Wait for all workers
        all_success = True
        for future in as_completed(futures):
            wid = futures[future]
            try:
                result = future.result()
                self.tracker.mark_worker(wid, "done")
                logger.info(f"Worker {wid} finished: {result} rows scraped")
            except Exception as e:
                self.tracker.mark_worker(wid, "error")
                logger.error(f"Worker {wid} failed: {e}")
                all_success = False

        self._executor.shutdown(wait=True)
        self._executor = None

        snap = self.tracker.snapshot()
        logger.info(
            f"All workers done: {snap['successful']}/{snap['total']} successful, "
            f"{snap['failed']} failed in {snap['elapsed_seconds']:.1f}s"
        )
        return all_success

    def _run_worker(self, worker_id: int, indices: list, df, column_mapping: dict) -> int:
        """Single worker thread: owns its own WebDriver."""
        # Stagger startup: each worker waits (worker_id * 2) seconds before
        # first request. Prevents all workers from slamming GSA simultaneously.
        if worker_id > 0:
            stagger = worker_id * 2  # 0s, 2s, 4s, 6s, 8s, ... 18s for 10 workers
            logger.info(f"Worker {worker_id} staggering startup by {stagger}s")
            if self.stop_event.wait(timeout=stagger):
                return 0  # stop was requested during stagger

        self.tracker.mark_worker(worker_id, "running")
        retries = 0

        def on_complete(part_number: str, success: bool):
            self.tracker.record(worker_id, part_number, success)

        automation = GSAScrapingAutomation(
            excel_file_path=EXCEL_FILE_PATH,
            stop_event=self.stop_event,
            rate_limiter=self.rate_limiter,  # None when proxies are used
            on_row_complete=on_complete,
            worker_id=worker_id,
            proxies=self.proxies,
        )

        while retries < SCRAPE_WORKER_MAX_RETRIES:
            try:
                automation.setup_driver()
                result = automation._execute_scraping_loop(indices, df, column_mapping)
                return result
            except Exception as e:
                retries += 1
                logger.error(
                    f"Worker {worker_id} crashed (attempt {retries}/{SCRAPE_WORKER_MAX_RETRIES}): {e}"
                )
                if self.stop_event.is_set():
                    break
                try:
                    if automation.driver:
                        automation.driver.quit()
                except Exception:
                    pass
            finally:
                try:
                    if automation.driver:
                        automation.driver.quit()
                except Exception:
                    pass

        logger.error(f"Worker {worker_id} exhausted all retries")
        return 0
