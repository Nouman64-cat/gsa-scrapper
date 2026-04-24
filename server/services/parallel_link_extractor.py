"""
Parallel Link Extraction Orchestrator
--------------------------------------
Mirrors ParallelScrapingOrchestrator but for the link-extraction pipeline.

Each worker gets its own InternalLinkScraper (and therefore its own Chrome
WebDriver).  Links from imported_links are partitioned across workers using
interleaved slicing so every worker gets a roughly equal share.

Usage:
    orchestrator = ParallelLinkExtractionOrchestrator(
        num_workers=3, sort_order="low_to_high"
    )
    orchestrator.run_full()
"""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# Hard cap: opening many Chrome windows is memory-intensive
_LINK_MAX_WORKERS = 5


def _resolve_workers(requested: int = 0) -> int:
    """Pick actual worker count.  0 means auto-detect from CPU count."""
    if requested > 0:
        return min(requested, _LINK_MAX_WORKERS)
    cpu = os.cpu_count() or 2
    return min(max(1, cpu // 2), _LINK_MAX_WORKERS)


# ─────────────────────────────────────────────────────────────────────────────

class LinkExtractionProgressTracker:
    """Thread-safe progress aggregator for parallel link extraction."""

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

    def record(self, worker_id: int, link_url: str, success: bool):
        with self._lock:
            self.completed += 1
            if success:
                self.successful += 1
            else:
                self.failed += 1
            ws = self._worker_status.get(worker_id, {})
            ws["completed"] = ws.get("completed", 0) + 1
            ws["current_part"] = link_url
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
            active = sum(
                1 for w in self._worker_status.values() if w["status"] == "running"
            )
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


# ─────────────────────────────────────────────────────────────────────────────

class ParallelLinkExtractionOrchestrator:
    """Coordinates multiple InternalLinkScraper workers in parallel threads."""

    def __init__(self, num_workers: int = 0, sort_order: str = "low_to_high", stop_after: int = 0):
        self.sort_order = sort_order
        self.stop_event = threading.Event()
        self.tracker: LinkExtractionProgressTracker | None = None
        self._executor: ThreadPoolExecutor | None = None
        self.num_workers = _resolve_workers(num_workers)
        self.stop_after = stop_after  # 0 = no limit
        logger.info(
            f"ParallelLinkExtractionOrchestrator: {self.num_workers} workers, "
            f"sort_order={sort_order}"
        )

    def stop(self):
        """Signal all workers to stop cleanly."""
        logger.info("ParallelLinkExtractionOrchestrator: broadcasting stop.")
        self.stop_event.set()
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def progress_snapshot(self) -> dict | None:
        if self.tracker:
            return self.tracker.snapshot()
        return None

    # ── public run modes ──────────────────────────────────────────────────────

    def run_full(self) -> bool:
        """
        Process all unscraped links in parallel.

        Phase 1 – product_detail links  → InternalLinkScraper
        Phase 2 – search/external links → SearchLinkScraper

        Both phases run with the same worker pool size; they run sequentially
        so that progress tracking stays accurate and Chrome memory usage stays
        bounded.
        """
        from database.db import get_engine
        from database.repository import get_all_product_detail_links, get_all_search_links

        engine = get_engine()
        product_links = get_all_product_detail_links(engine)
        search_links = get_all_search_links(engine)

        if not product_links and not search_links:
            logger.info("ParallelLinkExtractionOrchestrator: no links to process.")
            return True

        all_ok = True
        if product_links:
            logger.info(
                f"Phase 1: dispatching {len(product_links)} product_detail link(s) "
                f"to InternalLinkScraper workers."
            )
            all_ok &= self._dispatch(product_links, scraper_type="internal")

        if search_links:
            logger.info(
                f"Phase 2: dispatching {len(search_links)} search link(s) "
                f"to SearchLinkScraper workers."
            )
            all_ok &= self._dispatch(search_links, scraper_type="search")

        return all_ok

    def run_test(self, item_limit: int = 3) -> bool:
        """Process the first N unscraped product_detail links (for testing)."""
        from database.db import get_engine
        from database.repository import get_all_product_detail_links
        links = get_all_product_detail_links(get_engine())[:item_limit]
        if not links:
            logger.info("ParallelLinkExtractionOrchestrator: no links to process.")
            return True
        return self._dispatch(links, scraper_type="internal")

    # ── internal ──────────────────────────────────────────────────────────────

    def _dispatch(self, links: list, scraper_type: str = "internal") -> bool:
        """Partition links across workers and run them concurrently."""
        total = len(links)
        actual_workers = min(self.num_workers, total)
        if actual_workers < 1:
            actual_workers = 1

        self.tracker = LinkExtractionProgressTracker(total, actual_workers, self.stop_event, self.stop_after)

        # Interleaved slicing: worker-0 gets [0,N,2N,...], worker-1 gets [1,N+1,...]
        chunks = [links[i::actual_workers] for i in range(actual_workers)]

        logger.info(
            f"Dispatching {total} {scraper_type} link(s) across {actual_workers} worker(s) "
            f"(chunk sizes: {[len(c) for c in chunks]})"
        )

        self._executor = ThreadPoolExecutor(max_workers=actual_workers)
        futures = {
            self._executor.submit(self._run_worker, wid, chunk, scraper_type): wid
            for wid, chunk in enumerate(chunks)
        }

        all_success = True
        for future in as_completed(futures):
            wid = futures[future]
            try:
                result = future.result()
                self.tracker.mark_worker(wid, "done")
                logger.info(f"Worker {wid} finished: {result} link(s) extracted.")
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

    def _run_worker(self, worker_id: int, links: list, scraper_type: str = "internal") -> int:
        """
        Single worker thread.  Creates its own scraper (InternalLinkScraper for
        product_detail links, SearchLinkScraper for search links), processes its
        partition, then shuts down cleanly.
        """
        # Stagger startup so not all browsers launch simultaneously
        if worker_id > 0:
            stagger = worker_id * 3   # 3 s, 6 s, 9 s, 12 s
            logger.info(f"Worker {worker_id} staggering startup by {stagger}s")
            if self.stop_event.wait(timeout=stagger):
                return 0  # stop was requested during stagger

        self.tracker.mark_worker(worker_id, "running")

        def on_complete(link_url: str, success: bool):
            self.tracker.record(worker_id, link_url, success)

        if scraper_type == "search":
            from services.search_link_scraper import SearchLinkScraper
            scraper = SearchLinkScraper(
                sort_order=self.sort_order,
                stop_event=self.stop_event,
                on_row_complete=on_complete,
                worker_id=worker_id,
            )
        else:
            from services.internal_link_scraper import InternalLinkScraper
            scraper = InternalLinkScraper(
                sort_order=self.sort_order,
                stop_event=self.stop_event,
                on_row_complete=on_complete,
                worker_id=worker_id,
            )

        try:
            return scraper.run_assigned_links(links)
        except Exception as e:
            logger.error(f"Worker {worker_id} crashed: {e}")
            return 0
        finally:
            scraper._quit_driver()
