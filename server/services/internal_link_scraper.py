"""
Internal Link Scraper
---------------------
Handles product_detail links from the imported_links table.

Flow for each link (is_product_detail=True):
  1. Open the product detail URL in Chrome.
  2. Click the "Compare Available Sources" button (right side of page).
  3. Inside the compare modal, read the "Currently Selected" section to get:
       - Manufacturer Part Name
       - Manufacturer Part Number
  4. Read all rows from the comparison table.
     - sort_order="low_to_high"  → rows already ascending by price, read top→bottom.
     - sort_order="high_to_low"  → click the Price/Unit header to sort descending,
                                   then read top→bottom.
  5. For each row, extract: price, unit, contractor name.
  6. Click the contractor name link → popup appears with Contract #.
     Scrape the contract number, then close the popup.
  7. Save all rows into links_scraped_data DB table.
  8. Mark the ImportedLink as is_scraped=True.
"""

import logging
import random
import re
import sys
import os
import time
import urllib.parse
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.db import get_engine
from database.repository import (
    clear_links_scraped_data_for_link,
    get_all_product_detail_links,
    insert_link_scraped_rows,
    mark_imported_link_scraped,
)
from settings import PAGE_LOAD_TIMEOUT, SCRAPE_DELAY_SECONDS

logger = logging.getLogger(__name__)

# Maximum compare-table rows to scrape per product_detail link
MAX_ROWS_PER_LINK = 6

# ─────────────────────────────────────────────────────────────────────────────
# Selector constants  (multiple fallbacks; first match wins)
# ─────────────────────────────────────────────────────────────────────────────

_COMPARE_BTN_SELECTORS = [
    # normalize-space(.) matches the full text content including nested <span>/<b>/<i> children.
    # This is more reliable than text() which only matches direct text nodes.
    (By.XPATH, "(//button | //a)[contains(normalize-space(.), 'Compare Available Sources')]"),
    (By.XPATH, "//*[contains(text(), 'Compare Available Sources')]"),
    (By.XPATH, "(//button | //a)[contains(normalize-space(.), 'Compare Sources')]"),
    (By.XPATH, "//*[contains(text(), 'Compare Sources')]"),
    (By.XPATH, "//*[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'compare available')]"),
    (By.XPATH, "//*[contains(@class,'compare') and (self::button or self::a)]"),
    (By.CSS_SELECTOR, "button.compareBtn"),
    (By.CSS_SELECTOR, "a.compareBtn"),
    (By.CSS_SELECTOR, "[class*='compare-source']"),
    (By.CSS_SELECTOR, "[class*='compareSource']"),
    (By.CSS_SELECTOR, "[ng-click*='compare']"),
]

_COMPARE_TABLE_ROW_SELECTORS = [
    # Most specific: GSA vendor-list-table (confirmed from live page HTML)
    (By.CSS_SELECTOR, "table.vendor-list-table tbody tr"),
    (By.CSS_SELECTOR, "app-ux-compare-sources table tbody tr"),
    (By.CSS_SELECTOR, ".compare-sources-modal table tbody tr"),
    (By.CSS_SELECTOR, "modal table tbody tr"),
    (By.CSS_SELECTOR, ".modal-body table tbody tr"),
    (By.CSS_SELECTOR, ".modal table tbody tr"),
    (By.XPATH, "//div[contains(@class,'modal')]//table//tbody//tr"),
    (By.CSS_SELECTOR, "table tbody tr"),
]

_MODAL_CLOSE_SELECTORS = [
    (By.CSS_SELECTOR, ".modal-header button.close"),
    (By.CSS_SELECTOR, ".modal .close"),
    (By.CSS_SELECTOR, "[aria-label='Close']"),
    (By.CSS_SELECTOR, "button.close"),
    (By.XPATH, "//button[contains(@class,'close')]"),
    (By.XPATH, "//button[text()='×']"),
]

_PRICE_SORT_HEADER_SELECTORS = [
    (By.XPATH, "//th[contains(., 'Price')]"),
    (By.XPATH, "//th[contains(., 'Price/Unit')]"),
    (By.CSS_SELECTOR, "th.price"),
    (By.XPATH, "//span[contains(@class,'sort') and contains(ancestor::th,'.')]"),
]

# ─────────────────────────────────────────────────────────────────────────────


class InternalLinkScraper:
    """Scrapes product_detail links via the Compare Available Sources modal."""

    def __init__(
        self,
        sort_order: str = "low_to_high",
        stop_event=None,
        on_row_complete=None,
        worker_id: Optional[int] = None,
        headless: bool = True,
    ):
        self.sort_order = sort_order
        self._stop_event = stop_event
        self._stop_flag = False
        self._on_row_complete = on_row_complete
        self._worker_id = worker_id
        self._headless = headless
        self.driver = None
        self.wait = None
        self.engine = get_engine()
        self._wid = f"[ILS-W{worker_id}] " if worker_id is not None else "[ILS] "

    # ── stop handling ─────────────────────────────────────────────────────────

    @property
    def stop_requested(self) -> bool:
        if self._stop_event is not None:
            return self._stop_event.is_set()
        return self._stop_flag

    def stop(self):
        if self._stop_event is not None:
            self._stop_event.set()
        else:
            self._stop_flag = True

    # ── driver management ─────────────────────────────────────────────────────

    def setup_driver(self):
        """Initialize Chrome driver with the same stealth options as the main scraper."""
        opts = Options()

        if self._headless:
            opts.add_argument("--headless=new")
            opts.add_argument("--window-size=1920,1080")

        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Edge/122.0.0.0 Safari/537.36",
        ]
        opts.add_argument(f"user-agent={random.choice(user_agents)}")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--disable-background-timer-throttling")
        opts.add_argument("--disable-backgrounding-occluded-windows")
        opts.add_argument("--disable-renderer-backgrounding")
        opts.add_argument("--memory-pressure-off")
        # Do NOT disable images — they aren't needed but GSA Angular needs the page to render
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        self.driver = webdriver.Chrome(options=opts)
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self.wait = WebDriverWait(self.driver, PAGE_LOAD_TIMEOUT)
        logger.info(f"{self._wid}Chrome driver ready.")

    def _ensure_driver(self):
        """Reinitialize driver if it has crashed or been closed."""
        if self.driver is None:
            self.setup_driver()
            return
        try:
            _ = self.driver.title
        except Exception:
            logger.warning(f"{self._wid}Driver detached — reinitializing.")
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            self.setup_driver()

    def _quit_driver(self):
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None

    # ── public entry points ───────────────────────────────────────────────────

    def run_assigned_links(self, links: list) -> int:
        """
        Called by ParallelLinkExtractionOrchestrator to process a pre-assigned
        partition of links.  The driver must already be set up by the caller
        (or this method sets it up itself).
        """
        if not self.driver:
            self.setup_driver()
        return self._run_loop(links)

    def run_full(self):
        """Scrape all unscraped product_detail links."""
        links = get_all_product_detail_links(self.engine)
        if not links:
            logger.info(f"{self._wid}No product_detail links to scrape.")
            return True
        logger.info(f"{self._wid}Starting internal link scraping: {len(links)} links.")
        try:
            self.setup_driver()
            self._run_loop(links)
            return True
        except Exception as e:
            logger.error(f"{self._wid}run_full error: {e}")
            return False
        finally:
            self._quit_driver()

    def run_test(self, item_limit: int = 3):
        """Scrape the first N unscraped product_detail links."""
        links = get_all_product_detail_links(self.engine)[:item_limit]
        if not links:
            logger.info(f"{self._wid}No product_detail links to scrape.")
            return True
        try:
            self.setup_driver()
            self._run_loop(links)
            return True
        except Exception as e:
            logger.error(f"{self._wid}run_test error: {e}")
            return False
        finally:
            self._quit_driver()

    # ── core loop ─────────────────────────────────────────────────────────────

    def _run_loop(self, links: list) -> int:
        """Process a list of ImportedLink records. Returns count of successes."""
        total = len(links)
        successful = 0

        for offset, link_rec in enumerate(links, 1):
            if self.stop_requested:
                logger.warning(f"{self._wid}Stop requested — exiting loop.")
                break

            url = link_rec.link
            logger.info(f"{self._wid}[{offset}/{total}] Processing: {url}")

            try:
                self._ensure_driver()

                # Clear any previously stored data for this link
                clear_links_scraped_data_for_link(self.engine, link_rec.id)

                rows = self._scrape_product_detail_page(url)

                if rows:
                    insert_link_scraped_rows(self.engine, link_rec.id, url, rows)
                    successful += 1
                    logger.info(f"{self._wid}Saved {len(rows)} row(s) for link id={link_rec.id}")
                else:
                    logger.warning(f"{self._wid}No data scraped for link id={link_rec.id}")

                mark_imported_link_scraped(self.engine, link_rec.id)

                if self._on_row_complete:
                    self._on_row_complete(url, bool(rows))

            except Exception as e:
                logger.error(f"{self._wid}Error on link id={link_rec.id}: {e}")
                self._quit_driver()  # fresh driver on next iteration
                if self._on_row_complete:
                    self._on_row_complete(url, False)

            # Polite delay between pages
            delay = float(SCRAPE_DELAY_SECONDS) * random.uniform(1.0, 1.5)
            if self._stop_event is not None:
                self._stop_event.wait(timeout=delay)
            else:
                time.sleep(delay)

        logger.info(
            f"{self._wid}Loop done: {successful}/{total} successful."
        )
        return successful

    # ── per-page scraping ─────────────────────────────────────────────────────

    def _scrape_product_detail_page(self, url: str) -> list[dict]:
        """
        Open a product detail page, click Compare Available Sources, extract all rows.
        Returns a list of dicts ready for insert_link_scraped_rows().

        Failure chain (each step is a fallback for the previous):
          1. Compare modal rows  ← primary path
          2. Inline vendor-list-table on the page (no modal needed)
          3. Direct page extraction using URL contractNumber + visible price
        """
        try:
            logger.info(f"{self._wid}Loading: {url}")
            self.driver.get(url)
            self._wait_for_page_ready(timeout=15)
            time.sleep(2)

            # ── Step 1: read MPN from the 'About This Item' spec table ────────
            page_mfr_part_num = self._read_manufacturer_part_number_from_page()
            logger.info(f"{self._wid}Page-level MPN: {page_mfr_part_num!r}")

            # ── Step 2: click "Compare Available Sources" ─────────────────────
            btn_clicked = self._click_compare_sources_button()
            if not btn_clicked:
                logger.warning(
                    f"{self._wid}Compare button not found — will try inline table "
                    "and page-direct extraction as fallbacks."
                )
            else:
                # Modal needs time to open and Angular needs time to render its data
                time.sleep(2)
                self._wait_for_compare_table(timeout=15)

            # ── Step 3: read manufacturer info from "Currently Selected" header
            mfr_name, modal_mfr_part_num = self._read_currently_selected_info()
            mfr_part_num = page_mfr_part_num or modal_mfr_part_num

            # ── Step 4: extract compare rows (modal or inline table) ──────────
            rows = self._extract_all_compare_rows(mfr_name, mfr_part_num)

            # ── Step 5: fallback — read product info directly from the page ───
            # Covers cases where:
            #   • The compare button was never found (single-vendor item, auth wall)
            #   • The modal opened but returned 0 rows (structure mismatch)
            #   • The URL contains contractNumber= we can read directly
            if not rows:
                logger.info(
                    f"{self._wid}Compare approach yielded no rows — "
                    "falling back to direct page extraction."
                )
                rows = self._scrape_page_directly(url, mfr_name, mfr_part_num)

            return rows

        except Exception as e:
            logger.error(f"{self._wid}_scrape_product_detail_page failed for {url}: {e}")
            self._quit_driver()
            return []

    # ── page helpers ──────────────────────────────────────────────────────────

    def _wait_for_page_ready(self, timeout: int = 10):
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException:
            logger.warning(f"{self._wid}Page readyState timeout — continuing anyway.")

    def _wait_for_compare_table(self, timeout: int = 15):
        """Wait for the compare table to appear AND contain at least one price row."""
        def table_has_price_data(driver):
            try:
                tables = driver.find_elements(By.CSS_SELECTOR, "table")
                for table in tables:
                    if re.search(r"\$\s*[\d,]+", table.text or ""):
                        return True
            except Exception:
                pass
            return False

        try:
            WebDriverWait(self.driver, timeout).until(table_has_price_data)
            logger.info(f"{self._wid}Compare table with price data confirmed.")
        except TimeoutException:
            logger.warning(f"{self._wid}Compare table did not appear with data within {timeout}s.")

    # ── Direct page fallback ──────────────────────────────────────────────────

    def _scrape_page_directly(
        self,
        url: str,
        mfr_name: Optional[str],
        mfr_part_num: Optional[str],
    ) -> list[dict]:
        """
        Fallback extraction used when the Compare Available Sources modal yields
        nothing.  Two complementary approaches:

        A. URL params: GSA product detail URLs often carry contractNumber= directly.
           We parse it from the URL without any additional page interaction.

        B. Page body: The currently-selected contractor's price is rendered on the
           product detail page itself (before any modal click).  We parse the first
           dollar-sign price and unit from the visible text.

        Returns a list with at most one row dict, or [] if nothing useful is found.
        """
        try:
            # ── A. Contract number from URL query string ───────────────────────
            contract_number: Optional[str] = None
            m = re.search(r'[?&]contractNumber=([^&#]+)', url, re.IGNORECASE)
            if m:
                contract_number = urllib.parse.unquote(m.group(1)).strip()
                logger.info(f"{self._wid}Fallback: contract# from URL = {contract_number!r}")

            # ── B. Price / unit from page body text ───────────────────────────
            body_text: str = ""
            try:
                body_text = self.driver.find_element(By.TAG_NAME, "body").text
            except Exception:
                pass

            price = self._parse_price_text(body_text) if body_text else None
            unit = self._parse_unit_text(body_text) if body_text else None

            # Contract number from body text if still missing
            if not contract_number and body_text:
                contract_number = self._parse_contract_number(body_text)

            # Contractor name from known DOM selectors
            contractor_name = self._read_contractor_name_from_page()

            if price is not None or contract_number:
                row = {
                    "manufacturer_part_name": mfr_name,
                    "manufacturer_part_number": mfr_part_num,
                    "price": price,
                    "unit": unit,
                    "contractor_name": contractor_name,
                    "contract_number": contract_number,
                    "row_order": 0,
                }
                logger.info(
                    f"{self._wid}Fallback row: price={price}, unit={unit!r}, "
                    f"contractor={contractor_name!r}, contract#={contract_number!r}"
                )
                return [row]

        except Exception as e:
            logger.debug(f"{self._wid}_scrape_page_directly failed: {e}")

        return []

    def _read_contractor_name_from_page(self) -> Optional[str]:
        """
        Try to read the contractor/vendor name that is directly visible on the
        product detail page (without opening the compare modal).
        """
        _css_selectors = [
            "app-vendor-name",
            "[class*='vendorName']",
            "[class*='vendor-name']",
            "[class*='contractorName']",
            "[class*='contractor-name']",
            "div.col-sm-6.text-sm-left",
            "div.text-sm-left.col-sm-6",
        ]
        for sel in _css_selectors:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    txt = (el.text or "").strip()
                    # Skip values that look like contract numbers or are too short
                    if txt and len(txt) > 3 and not re.match(r'^[A-Z0-9]{6,20}$', txt):
                        return txt
            except Exception:
                pass
        return None

    def _click_compare_sources_button(self) -> bool:
        """Find and click the Compare Available Sources button.

        GSA Advantage is an Angular SPA — the button is only injected into the
        DOM after Angular finishes bootstrapping and its API calls resolve, which
        happens *after* document.readyState == 'complete'.  We therefore wait up
        to 25 seconds for any matching selector to become visible before
        attempting the click.
        """
        def any_compare_btn_visible(driver):
            for sel_type, sel_val in _COMPARE_BTN_SELECTORS:
                try:
                    els = driver.find_elements(sel_type, sel_val)
                    for el in els:
                        try:
                            if el.is_displayed():
                                return True
                        except Exception:
                            pass
                except Exception:
                    pass
            return False

        try:
            WebDriverWait(self.driver, 25).until(any_compare_btn_visible)
            logger.info(f"{self._wid}Compare button appeared in DOM — attempting click.")
        except TimeoutException:
            logger.warning(f"{self._wid}Compare button did not appear within 25s.")
            return False

        for sel_type, sel_val in _COMPARE_BTN_SELECTORS:
            try:
                el = self.driver.find_element(sel_type, sel_val)
                if el and el.is_displayed():
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", el)
                    time.sleep(0.4)
                    try:
                        el.click()
                    except ElementClickInterceptedException:
                        self.driver.execute_script("arguments[0].click();", el)
                    logger.info(f"{self._wid}Clicked Compare button via {sel_type}={sel_val!r}")
                    return True
            except (NoSuchElementException, Exception):
                continue
        return False

    def _click_price_header_to_sort_descending(self):
        """
        Click the Price/Unit column header in the compare table to switch to
        descending order (high → low). GSA Advantage toggles asc/desc on each click;
        one click is enough if the default is ascending.
        """
        for sel_type, sel_val in _PRICE_SORT_HEADER_SELECTORS:
            try:
                el = self.driver.find_element(sel_type, sel_val)
                if el and el.is_displayed():
                    el.click()
                    logger.info(f"{self._wid}Clicked Price sort header — now descending.")
                    return
            except (NoSuchElementException, Exception):
                continue
        logger.warning(f"{self._wid}Price sort header not found — reading rows top→bottom regardless.")

    # ── Page-level product info ───────────────────────────────────────────────

    def _read_manufacturer_part_number_from_page(self) -> Optional[str]:
        """
        Extract Manufacturer Part Number from the product detail page's
        'About This Item' specification table, which is visible before any
        modal is opened.

        The GSA Advantage product detail page renders a spec table like:

            | Manufacturer Part Number | D5600XDAHC |
            | Manufacturer             | PROMISE TECHNOLOGY |
            | Country of Origin        | TAIWAN             |
            ...

        We try several DOM strategies to read the value cell adjacent to the
        'Manufacturer Part Number' label.
        """
        selectors = [
            # th label → sibling td value  (most common layout)
            (By.XPATH,
             "//th[contains(normalize-space(.),'Manufacturer Part Number')]"
             "/following-sibling::td[1]"),
            # td label → sibling td value  (alternative layout)
            (By.XPATH,
             "//td[contains(normalize-space(.),'Manufacturer Part Number')]"
             "/following-sibling::td[1]"),
            # dt/dd definition-list layout
            (By.XPATH,
             "//dt[contains(normalize-space(.),'Manufacturer Part Number')]"
             "/following-sibling::dd[1]"),
            # Angular-specific: label inside a div followed by value div
            (By.XPATH,
             "//*[contains(normalize-space(.),'Manufacturer Part Number')]"
             "/following-sibling::*[1]"),
        ]
        for sel_type, sel_val in selectors:
            try:
                el = self.driver.find_element(sel_type, sel_val)
                txt = el.text.strip()
                if txt and txt.lower() not in ("manufacturer part number", ""):
                    return txt
            except (NoSuchElementException, Exception):
                continue
        return None

    # ── Currently Selected header ─────────────────────────────────────────────

    def _read_currently_selected_info(self) -> tuple[Optional[str], Optional[str]]:
        """
        Extract Manufacturer Name and Manufacturer Part Number from the
        'Currently Selected' section at the top of the compare sources modal.
        """
        mfr_name = None
        mfr_part_num = None

        try:
            # The "Currently Selected" block is usually a small table/div just
            # above the comparison rows, inside the modal.
            candidate_selectors = [
                ".currently-selected",
                "[class*='currently-selected']",
                "[class*='currentlySelected']",
                ".modal-body .selected-info",
                ".modal-body table:first-of-type",
            ]
            for sel in candidate_selectors:
                try:
                    block = self.driver.find_element(By.CSS_SELECTOR, sel)
                    text = block.text
                    mfr_name, mfr_part_num = self._parse_currently_selected_text(text)
                    if mfr_name or mfr_part_num:
                        logger.info(
                            f"{self._wid}Currently Selected → name={mfr_name!r}, "
                            f"part_num={mfr_part_num!r}"
                        )
                        return mfr_name, mfr_part_num
                except NoSuchElementException:
                    continue

            # Fallback: scan the entire modal text
            modal_text = ""
            for modal_sel in [".modal-body", ".modal", "[role='dialog']"]:
                try:
                    modal_text = self.driver.find_element(By.CSS_SELECTOR, modal_sel).text
                    break
                except NoSuchElementException:
                    continue

            if modal_text:
                mfr_name, mfr_part_num = self._parse_currently_selected_text(modal_text)

        except Exception as e:
            logger.warning(f"{self._wid}Could not read Currently Selected section: {e}")

        return mfr_name, mfr_part_num

    def _parse_currently_selected_text(self, text: str) -> tuple[Optional[str], Optional[str]]:
        """
        Parse free text from the Currently Selected header area.
        Looks for 'Manufacturer Name' and 'Manufacturer Part Number' labels.
        """
        mfr_name = None
        mfr_part_num = None

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        for i, line in enumerate(lines):
            low = line.lower()
            if "manufacturer name" in low and i + 1 < len(lines):
                mfr_name = lines[i + 1]
            elif "manufacturer part number" in low and i + 1 < len(lines):
                mfr_part_num = lines[i + 1]
            elif "manufacturer part" in low and i + 1 < len(lines) and not mfr_part_num:
                mfr_part_num = lines[i + 1]

        # Inline pattern: "Manufacturer Name: RACKMOUNT" on a single line
        if not mfr_name:
            m = re.search(r'manufacturer\s+name\s*[:\-]\s*(.+)', text, re.IGNORECASE)
            if m:
                mfr_name = m.group(1).split('\n')[0].strip()

        if not mfr_part_num:
            m = re.search(r'manufacturer\s+part\s+(?:number|#|no)\s*[:\-]\s*([^\s\n]+)',
                          text, re.IGNORECASE)
            if m:
                mfr_part_num = m.group(1).strip()

        return mfr_name or None, mfr_part_num or None

    # ── Table row extraction ──────────────────────────────────────────────────

    def _find_compare_table_rows(self) -> list:
        """
        Return all <tr> elements from the main compare-sources table.

        The modal often contains multiple tables (e.g. \"Currently Selected\" has its
        own small table). Generic selectors like ``table tbody tr`` match that first
        and yield a single row. We pick the table inside the visible modal that has
        the most rows containing a dollar price.
        """
        modal = None
        for sel in (".modal.show", ".modal.in", "[role='dialog']", ".modal"):
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, sel)
                if el.is_displayed():
                    modal = el
                    break
            except NoSuchElementException:
                continue

        if modal is not None:
            try:
                tables = modal.find_elements(By.CSS_SELECTOR, "table")
            except Exception:
                tables = []
            best_table = None
            best_price_rows = 0
            for table in tables:
                try:
                    trs = table.find_elements(By.CSS_SELECTOR, "tbody tr")
                except Exception:
                    continue
                n_price = 0
                for tr in trs:
                    t = (tr.text or "").strip().lower()
                    if re.search(r"\$\s*[\d,]+\.?\d*", t):
                        n_price += 1
                if n_price > best_price_rows:
                    best_price_rows = n_price
                    best_table = table

            if best_table is not None and best_price_rows > 0:
                rows = best_table.find_elements(By.CSS_SELECTOR, "tbody tr")
                logger.info(
                    f"{self._wid}Using compare table with {best_price_rows} price row(s), "
                    f"{len(rows)} <tr> total in modal."
                )
                self._scroll_compare_table_for_lazy_rows(modal, best_table)
                rows = best_table.find_elements(By.CSS_SELECTOR, "tbody tr")
                return rows

        for sel_type, sel_val in _COMPARE_TABLE_ROW_SELECTORS:
            try:
                rows = self.driver.find_elements(sel_type, sel_val)
                if rows:
                    logger.info(
                        f"{self._wid}Found {len(rows)} table rows "
                        f"via {sel_type}={sel_val!r}"
                    )
                    return rows
            except Exception:
                continue
        logger.warning(f"{self._wid}No compare table rows found with any selector.")
        return []

    def _scroll_compare_table_for_lazy_rows(self, modal, table) -> None:
        """Scroll the modal/table container so virtualized or lazy rows mount in the DOM."""
        try:
            self.driver.execute_script(
                "arguments[0].scrollTop = arguments[0].scrollHeight;",
                modal,
            )
            time.sleep(0.4)
            self.driver.execute_script(
                "arguments[0].scrollIntoView(false);", table
            )
            time.sleep(0.3)
        except Exception:
            pass

    def _extract_all_compare_rows(
        self,
        mfr_name: Optional[str],
        mfr_part_num: Optional[str],
    ) -> list[dict]:
        """
        Iterate rows in the compare table and build result dicts.

        Ordering strategy (table is always low→high by default on GSA):
          low_to_high  → read top→bottom, keep first 6 (cheapest 6)
          high_to_low  → reverse the list, read top→bottom, keep first 6
                         (= last 6 from original = most expensive 6)
        """
        table_rows = self._find_compare_table_rows()
        if not table_rows:
            return []

        if self.sort_order == "high_to_low":
            table_rows = list(reversed(table_rows))
            logger.info(f"{self._wid}sort_order=high_to_low → table rows reversed "
                        f"({len(table_rows)} total), reading bottom→top.")
        else:
            logger.info(f"{self._wid}sort_order=low_to_high → reading top→bottom "
                        f"({len(table_rows)} total rows).")

        results = []

        for idx, tr in enumerate(table_rows):
            if len(results) >= MAX_ROWS_PER_LINK:
                logger.info(f"{self._wid}Reached {MAX_ROWS_PER_LINK}-row limit — stopping.")
                break

            try:
                # Skip header rows (class="sources-header") and empty rows
                row_class = (tr.get_attribute("class") or "").lower()
                if "sources-header" in row_class:
                    continue
                if not tr.text.strip():
                    continue

                data = self._extract_row_data_from_dom(tr)
                if data is None:
                    # Fallback: text-based extraction
                    data = self._extract_row_data_from_text(tr)
                if data is None:
                    continue

                results.append({
                    "manufacturer_part_name": mfr_name,
                    "manufacturer_part_number": mfr_part_num,
                    "price": data["price"],
                    "unit": data["unit"],
                    "contractor_name": data["contractor_name"],
                    "contract_number": data["contract_number"],
                    "row_order": len(results),
                })

                logger.info(
                    f"{self._wid}Row {len(results)}/{MAX_ROWS_PER_LINK}: "
                    f"price={data['price']}, unit={data['unit']!r}, "
                    f"contractor={data['contractor_name']!r}, "
                    f"contract#={data['contract_number']!r}"
                )

            except Exception as e:
                logger.warning(f"{self._wid}Error parsing table row {idx}: {e}")
                continue

        return results

    # ── DOM-targeted row extraction ───────────────────────────────────────────

    def _extract_row_data_from_dom(self, tr) -> Optional[dict]:
        """
        Extract price, unit, contractor name and contract number using the
        exact DOM structure confirmed from the live GSA vendor-list-table:

          <tr class="otherItem | selectedItem">
            <td>  Select button        </td>
            <td>  <strong>$8.01</strong>   </td>   ← price
            <td>  <a aria-label="Unit Definitions Modal EA">EA</a>  </td>  ← unit
            <td>  feature icons         </td>
            <td>  <a title="Link to contractor info"
                     href="...?contractNumber=47QTCA22D00DM">
                    PINE + PLUG LLC
                  </a>
                  OR (for the currently-selected row):
                  <b>CONTRACTOR NAME</b>
            </td>
            …
          </tr>

        The contract number lives directly in the contractor <a> href query
        parameter — no popup click required.
        """
        try:
            # ── Price ─────────────────────────────────────────────────────────
            price: Optional[float] = None
            try:
                strong_el = tr.find_element(By.CSS_SELECTOR, "td strong")
                price = self._parse_price_text(strong_el.text)
            except NoSuchElementException:
                pass

            # ── Unit ──────────────────────────────────────────────────────────
            unit: Optional[str] = None
            try:
                unit_a = tr.find_element(
                    By.CSS_SELECTOR,
                    "a[aria-label^='Unit Definitions Modal']",
                )
                unit = unit_a.text.strip().upper() or None
                if not unit:
                    # Parse from aria-label: "Unit Definitions Modal EA"
                    lbl = (unit_a.get_attribute("aria-label") or "").replace(
                        "Unit Definitions Modal", ""
                    ).strip()
                    unit = lbl.upper() or None
            except NoSuchElementException:
                pass

            # ── Contractor name + contract number from href ───────────────────
            contractor_name: Optional[str] = None
            contract_number: Optional[str] = None

            try:
                clink = tr.find_element(
                    By.CSS_SELECTOR, "a[title='Link to contractor info']"
                )
                contractor_name = clink.text.strip() or None
                href = clink.get_attribute("href") or ""
                m = re.search(r'contractNumber=([A-Za-z0-9\-]+)', href)
                if m:
                    contract_number = m.group(1).strip()
            except NoSuchElementException:
                # "selectedItem" row shows the contractor in <b>, no link
                try:
                    b_el = tr.find_element(By.CSS_SELECTOR, "td b")
                    contractor_name = b_el.text.strip() or None
                except NoSuchElementException:
                    pass
                # Try to derive contract # from product-photo img src
                if not contract_number:
                    try:
                        img = tr.find_element(
                            By.CSS_SELECTOR, "app-product-photo img[src*='/products/']"
                        )
                        src = img.get_attribute("src") or ""
                        m2 = re.search(r'/products/([^/]+)/', src)
                        if m2:
                            contract_number = m2.group(1)
                    except NoSuchElementException:
                        pass

            if price is None and not contractor_name:
                return None

            return {
                "price": price,
                "unit": unit,
                "contractor_name": contractor_name,
                "contract_number": contract_number,
            }

        except Exception as e:
            logger.debug(f"{self._wid}_extract_row_data_from_dom error: {e}")
            return None

    def _extract_row_data_from_text(self, tr) -> Optional[dict]:
        """Text-based fallback parser for rows that don't match the DOM pattern."""
        row_text = tr.text.strip()
        if not row_text:
            return None

        price = self._parse_price_text(row_text)
        unit = self._parse_unit_text(row_text)
        contractor_name = self._get_contractor_link_text(tr)

        if price is None and not contractor_name:
            return None

        # Try to get contract number from the href without clicking
        contract_number = self._extract_contract_from_href(tr)

        return {
            "price": price,
            "unit": unit,
            "contractor_name": contractor_name,
            "contract_number": contract_number,
        }

    # ── price / unit / contractor helpers ────────────────────────────────────

    def _parse_price_text(self, text: str) -> Optional[float]:
        for pat in [
            r'\$\s*([\d,]+\.?\d*)',
            r'([\d,]+\.\d{2})\s*(?:EA|BX|BT|PK|DZ|CS|PR|SE|LO|KT)',
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1).replace(',', ''))
                except ValueError:
                    continue
        return None

    # Keep legacy alias used by _extract_row_data_from_text
    def _parse_price(self, text: str) -> Optional[float]:
        return self._parse_price_text(text)

    def _parse_unit_text(self, text: str) -> Optional[str]:
        for line in text.splitlines():
            m = re.search(r'\$\s*[\d,]+\.?\d*\s+([A-Za-z]{2,4})\b', line)
            if m:
                return m.group(1).upper()
            m = re.search(r'\$\s*[\d,]+\.?\d*\s*/\s*([A-Za-z]{1,4})\b', line)
            if m:
                return m.group(1).upper()
        return None

    # Keep legacy alias
    def _parse_unit(self, text: str) -> Optional[str]:
        return self._parse_unit_text(text)

    def _get_contractor_link_text(self, tr) -> Optional[str]:
        """Return the text of the contractor <a> link inside this <tr> (fallback)."""
        _skip = {"select", "details", "more info", "info", "compare"}
        try:
            links = tr.find_elements(By.TAG_NAME, "a")
            for lnk in links:
                txt = lnk.text.strip()
                if txt and len(txt) > 2 and txt.lower() not in _skip:
                    if not re.match(r'^\$?[\d,]+\.?\d*$', txt):
                        return txt
        except Exception:
            pass
        return None

    def _extract_contract_from_href(self, tr) -> Optional[str]:
        """Extract contract number from the contractor link href (no click needed)."""
        try:
            links = tr.find_elements(By.TAG_NAME, "a")
            for lnk in links:
                href = lnk.get_attribute("href") or ""
                m = re.search(r'contractNumber=([A-Za-z0-9\-]+)', href)
                if m:
                    return m.group(1).strip()
        except Exception:
            pass
        return None

    def _read_contract_number_from_current_view(self) -> Optional[str]:
        """
        Scrape the contract number from either:
          - The contractor info modal overlay, or
          - A new page that opened after clicking the contractor link.

        GSA often renders the contract # in a div with classes ``col-sm-6 text-sm-left``.
        The contractor detail page / modal may also show: Contract: 47QTCA23D00CC
        """
        time.sleep(0.8)

        # Primary: labeled layout column used on GSA contractor popups
        try:
            col_selectors = (
                "div.col-sm-6.text-sm-left",
                "div.text-sm-left.col-sm-6",
                "div[class*='col-sm-6'][class*='text-sm-left']",
            )
            seen = set()
            for css in col_selectors:
                for el in self.driver.find_elements(By.CSS_SELECTOR, css):
                    id_el = id(el)
                    if id_el in seen:
                        continue
                    seen.add(id_el)
                    txt = (el.text or "").strip()
                    if not txt:
                        continue
                    cn = self._parse_contract_number(txt)
                    if cn:
                        logger.info(
                            f"{self._wid}Contract# from col-sm-6 text-sm-left: {cn!r}"
                        )
                        return cn
        except Exception:
            pass

        # Try modal-scoped selectors
        modal_selectors = [
            ".modal-body",
            ".modal",
            "[role='dialog']",
            ".contractor-info",
            "[class*='contractorInfo']",
            "[class*='contractor-info']",
        ]
        for sel in modal_selectors:
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, sel)
                cn = self._parse_contract_number(el.text)
                if cn:
                    return cn
            except NoSuchElementException:
                continue

        # Fallback: full page body
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            return self._parse_contract_number(body_text)
        except Exception:
            return None

    def _parse_contract_number(self, text: str) -> Optional[str]:
        """
        Extract a GSA contract number from a block of text.
        GSA contract numbers look like: 47QTCA23D00CC  (6–20 uppercase alphanum chars).
        """
        patterns = [
            r'contract\s*[:#\-]?\s*([A-Z0-9]{6,20})',
            r'contract\s+number\s*[:#\-]?\s*([A-Z0-9]{6,20})',
            r'contract\s+#\s*([A-Z0-9]{6,20})',
            r'(?:^|\s)(47[A-Z0-9]{4,18})\s*$',  # line is often just the contract id
            r'(?:^|\s)(47[A-Z0-9]{4,18})(?:\s|$)',
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
            if m:
                candidate = m.group(1).strip()
                if re.match(r'^[A-Z0-9]{6,20}$', candidate):
                    return candidate
        return None

    def _close_modal(self):
        """Close a popup / modal on the current page."""
        for sel_type, sel_val in _MODAL_CLOSE_SELECTORS:
            try:
                el = self.driver.find_element(sel_type, sel_val)
                if el and el.is_displayed():
                    el.click()
                    time.sleep(0.5)
                    return
            except (NoSuchElementException, Exception):
                continue
        # Last resort: press Escape
        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
        except Exception:
            pass
