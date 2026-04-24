import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException

import state
from database.db import get_engine
from database.repository import (
    get_imported_parts_count,
    get_all_product_detail_links,
    get_all_search_links,
)
from services.parallel_scraper import ParallelScrapingOrchestrator
from services.parallel_link_extractor import ParallelLinkExtractionOrchestrator
from services.aws_service import notify_scraping_complete
from models.requests import ScrapingRequest, LinkExtractionRequest

router = APIRouter(prefix="/api/scrape", tags=["Scraping"])
logger = logging.getLogger(__name__)

VALID_MODES = {"test", "full", "missing", "custom"}


def _validate_range(start_row: int, end_row: int) -> None:
    if start_row < 1:
        raise HTTPException(status_code=422, detail="start_row must be >= 1.")
    if end_row < start_row:
        raise HTTPException(status_code=422, detail="end_row must be >= start_row.")
    if end_row - start_row > 50_000:
        raise HTTPException(status_code=422, detail="Range cannot exceed 50,000 rows.")


def _run_scraping(req: ScrapingRequest) -> None:
    """Background task: runs parallel scraping and resets state when done."""
    try:
        orchestrator = ParallelScrapingOrchestrator(
            num_workers=req.num_workers,
            sort_order=req.sort_order,
            stop_after=req.stop_after,
        )
        state.parallel_orchestrator = orchestrator

        if req.mode == "test":
            orchestrator.run_test(req.item_limit)
        elif req.mode == "full":
            orchestrator.run_full()
        elif req.mode == "missing":
            orchestrator.run_missing()
        elif req.mode == "custom":
            orchestrator.run_custom_range(req.start_row, req.end_row)

    except Exception as e:
        logger.error(f"Scraping background task error: {e}")
    finally:
        with state.state_lock:
            state.is_scraping_running = False
            state.active_scraping_automation = None
            state.parallel_orchestrator = None
        try:
            notify_scraping_complete()
        except Exception as e:
            logger.error(f"Post-scraping notification error: {e}")


@router.post("/start")
async def start_scraping(req: ScrapingRequest, background_tasks: BackgroundTasks):
    if req.mode not in VALID_MODES:
        raise HTTPException(status_code=422, detail=f"Invalid mode '{req.mode}'. Choose from: {VALID_MODES}")
    if req.mode == "custom":
        _validate_range(req.start_row, req.end_row)
    if req.item_limit < 1:
        raise HTTPException(status_code=422, detail="item_limit must be >= 1.")

    # Ensure data has been imported
    engine = get_engine()
    if get_imported_parts_count(engine) == 0:
        raise HTTPException(status_code=400, detail="No data imported. Please upload an Excel file first.")

    with state.state_lock:
        if state.is_scraping_running:
            raise HTTPException(status_code=400, detail="Scraping process is already actively running.")
        state.is_scraping_running = True

    background_tasks.add_task(_run_scraping, req)
    return {"status": "started", "message": f"Scraping mode '{req.mode}' has been queued."}


@router.post("/stop")
async def stop_scrape():
    if state.parallel_orchestrator:
        state.parallel_orchestrator.stop()
        return {"status": "stopping", "message": "Stop signal sent to all workers."}
    if state.active_scraping_automation:
        state.active_scraping_automation.stop()
        return {"status": "stopping", "message": "Scraping stop signal sent."}
    return {"status": "idle", "message": "Scraping is not running."}


# ── Link extraction endpoints (imported_links → links_scraped_data) ───────────

def _run_link_extraction(req: LinkExtractionRequest) -> None:
    """Background task: runs ParallelLinkExtractionOrchestrator and resets state."""
    try:
        orchestrator = ParallelLinkExtractionOrchestrator(
            num_workers=req.num_workers,
            sort_order=req.sort_order,
            stop_after=req.stop_after,
        )
        with state.state_lock:
            state.parallel_link_extractor = orchestrator
            state.active_link_extractor = orchestrator
        orchestrator.run_full()
    except Exception as e:
        logger.error(f"Link extraction background task error: {e}")
    finally:
        with state.state_lock:
            state.is_link_extraction_running = False
            state.active_link_extractor = None
            state.parallel_link_extractor = None
        try:
            notify_scraping_complete()
        except Exception as e:
            logger.error(f"Post-link-extraction notification error: {e}")


@router.post("/links/start")
async def start_link_extraction(req: LinkExtractionRequest, background_tasks: BackgroundTasks):
    """
    Start the link extraction pipeline.

    Handles two types of imported links:
    - product_detail links (is_product_detail=True): opens the product detail
      page, clicks Compare Available Sources, scrapes up to 6 rows from the
      comparison table via InternalLinkScraper.
    - search/external links (is_product_detail=False): opens the GSA Advantage
      search results URL, matches product cards by part number, and scrapes up
      to 6 cards via SearchLinkScraper.

    Both pipelines store results in links_scraped_data and honour the same
    sort_order setting.
    """
    engine = get_engine()
    product_links = get_all_product_detail_links(engine)
    search_links = get_all_search_links(engine)

    total_links = len(product_links) + len(search_links)
    if total_links == 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "No unscraped links available. Please upload a links Excel file first. "
                "The file should have 'Internal Link URL' columns for product detail links "
                "and/or 'External Link URL' + 'Manufacturer Part Number' for search links."
            )
        )

    with state.state_lock:
        if state.is_link_extraction_running:
            raise HTTPException(status_code=400, detail="Link extraction is already running.")
        state.is_link_extraction_running = True

    background_tasks.add_task(_run_link_extraction, req)
    return {
        "status": "started",
        "message": (
            f"Link extraction started: {len(product_links)} product_detail link(s), "
            f"{len(search_links)} search link(s)."
        ),
        "product_detail_links": len(product_links),
        "search_links": len(search_links),
        "total_links": total_links,
    }


@router.post("/links/stop")
async def stop_link_extraction():
    """Send a stop signal to all running link extraction workers."""
    if state.parallel_link_extractor:
        state.parallel_link_extractor.stop()
        return {"status": "stopping", "message": "Stop signal sent to all link extraction workers."}
    if state.active_link_extractor:
        state.active_link_extractor.stop()
        return {"status": "stopping", "message": "Stop signal sent to link extractor."}
    return {"status": "idle", "message": "Link extraction is not running."}
