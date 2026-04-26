"""
Shared automation state.

All route modules import from here to read/write the running flags and
hold references to the active automation objects.
"""
import threading

state_lock = threading.Lock()

# Phase 1 — GSA URL generation (imported_parts → gsa_links)
is_link_generation_running: bool = False
active_link_automation = None

# Phase 2a — Price extraction (gsa_links → gsa_scraped_data)
is_scraping_running: bool = False
active_scraping_automation = None
parallel_orchestrator = None

# Phase 2b — Link extraction (imported_links[is_product_detail] → links_scraped_data)
is_link_extraction_running: bool = False
active_link_extractor = None          # single-worker fallback (unused in current flow)
parallel_link_extractor = None        # ParallelLinkExtractionOrchestrator instance

# Job tracking — set on import, read when scraping starts
current_parts_job_id: int | None = None
current_links_job_id: int | None = None
