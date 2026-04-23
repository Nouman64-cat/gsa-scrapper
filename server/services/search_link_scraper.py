"""
Search Link Scraper
-------------------
Handles search/external links from the imported_links table
(is_product_detail=False).

Flow for each link:
  1. Open the GSA Advantage search result URL in Chrome.
  2. Wait for product cards to load.
  3. Collect all visible product cards.
  4. Normalize and match the part number stored in ImportedLink.part_number
     against the part number shown on each card (strips all non-alphanumeric
     characters before comparing, case-insensitive).
  5. Apply sort order:
       low_to_high  → take the first 6 matching cards (default table order,
                       lowest price first)
       high_to_low  → take the last 6 matching cards (reversed, highest
                       price first)
  6. For each of up to 6 cards extract:
       price, unit, manufacturer name, contractor name, contract number.
  7. Save rows into links_scraped_data and mark the link as scraped.
"""

import logging
import os
import random
import re
import sys
import time
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.db import get_engine
from database.models import ImportedLink
from database.repository import (
    clear_links_scraped_data_for_link,
    get_all_search_links,
    insert_link_scraped_rows,
    mark_imported_link_scraped,
)
from settings import PAGE_LOAD_TIMEOUT, SCRAPE_DELAY_SECONDS

logger = logging.getLogger(__name__)

MAX_CARDS_PER_LINK = 6

# ── Card container selectors (ordered most-specific → least-specific) ─────────

_CARD_SELECTORS = [
    (By.CSS_SELECTOR, "app-ux-product-display-inline"),
    (By.CSS_SELECTOR, ".productViewControl"),
    (By.CSS_SELECTOR, "app-ux-product-display"),
    (By.CSS_SELECTOR, "[class*='product-item']"),
    (By.CSS_SELECTOR, "[class*='result-item']"),
]

# ── Regex helpers ──────────────────────────────────────────────────────────────

_RE_PRICE = re.compile(r"\$\s*([\d,]+\.?\d*)")
_RE_UNIT_INLINE = re.compile(r"\$\s*[\d,]+\.?\d*\s+([A-Z]{1,4})\b")
_RE_MFR = re.compile(r"Mfr:\s*([^\n]+)", re.IGNORECASE)
_RE_CONTRACTOR = re.compile(r"Contractor:\s*\n?([^\n]+)", re.IGNORECASE)
_RE_CONTRACT_NUM_TEXT = re.compile(r"Contract\s*#?:?\s*([A-Z0-9]{6,})", re.IGNORECASE)
_RE_CONTRACT_HREF = re.compile(r"contractNumber=([^&]+)", re.IGNORECASE)
_RE_PART_NUMBER_LINE = re.compile(r"^[A-Z0-9][A-Z0-9\-\/\.]{2,}$", re.IGNORECASE)


def _normalize_part_number(pn: str) -> str:
    """Strip all non-alphanumeric characters and uppercase for matching."""
    return re.sub(r"[^A-Z0-9]", "", pn.upper())


# ─────────────────────────────────────────────────────────────────────────────


class SearchLinkScraper:
    """
    Scrapes GSA Advantage search result pages for external/search links.
    Shares the same interface as InternalLinkScraper so it can be used by
    the ParallelLinkExtractionOrchestrator.
    """

    def __init__(
        self,
        sort_order: str = "low_to_high",
        stop_event=None,
        on_row_complete=None,
        worker_id: Optional[int] = None,
    ):
        self.sort_order = sort_order
        self._stop_event = stop_event
        self._stop_flag = False
        self._on_row_complete = on_row_complete
        self._worker_id = worker_id
        self.driver = None
        self.wait = None
        self.engine = get_engine()
        self._wid = f"[SLS-W{worker_id}] " if worker_id is not None else "[SLS] "

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
        """Initialize a headless-optional Chrome driver with stealth options."""
        opts = Options()
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
        partition of search links.
        """
        if not self.driver:
            self.setup_driver()
        return self._run_loop(links)

    def run_full(self):
        """Scrape all unscraped search links."""
        links = get_all_search_links(self.engine)
        if not links:
            logger.info(f"{self._wid}No search links to scrape.")
            return True
        logger.info(f"{self._wid}Starting search link scraping: {len(links)} links.")
        try:
            self.setup_driver()
            self._run_loop(links)
            return True
        finally:
            self._quit_driver()

    # ── main loop ─────────────────────────────────────────────────────────────

    def _run_loop(self, links: list) -> int:
        """Iterate over links, scrape each one, report progress."""
        completed = 0
        for link_rec in links:
            if self.stop_requested:
                logger.info(f"{self._wid}Stop requested — halting.")
                break
            success = False
            try:
                success = self._scrape_search_page(link_rec)
                completed += 1
            except Exception as exc:
                logger.error(
                    f"{self._wid}Unhandled error on {link_rec.link}: {exc}",
                    exc_info=True,
                )
                try:
                    self._ensure_driver()
                except Exception:
                    pass
            finally:
                if self._on_row_complete:
                    self._on_row_complete(link_rec.link, success)
            time.sleep(SCRAPE_DELAY_SECONDS)
        return completed

    # ── per-link scraping ─────────────────────────────────────────────────────

    def _scrape_search_page(self, link_rec: ImportedLink) -> bool:
        """Open the search URL, find cards, extract up to 6, save to DB."""
        self._ensure_driver()
        clear_links_scraped_data_for_link(self.engine, link_rec.id)

        logger.info(f"{self._wid}Opening: {link_rec.link}")
        self.driver.get(link_rec.link)

        # Wait for page readiness
        try:
            WebDriverWait(self.driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException:
            logger.warning(f"{self._wid}Page readyState timeout, continuing.")
        time.sleep(2)

        # Wait until at least one card selector appears in the DOM
        self._wait_for_cards()

        # Wait for Angular to populate the cards with actual data (price, contractor).
        # Cards can appear as empty Angular shells before data-binding completes.
        self._wait_for_card_data()

        # Gentle human-like scroll to trigger lazy rendering of off-screen cards
        try:
            self.driver.execute_script("window.scrollBy(0, 400);")
            time.sleep(0.6)
            self.driver.execute_script("window.scrollBy(0, -200);")
            time.sleep(0.4)
        except Exception:
            pass

        cards = self._find_cards()
        if not cards:
            logger.warning(f"{self._wid}No product cards found on {link_rec.link}")
            mark_imported_link_scraped(self.engine, link_rec.id)
            return False

        logger.info(f"{self._wid}Found {len(cards)} card(s) on page.")

        # Filter by part number match
        target_pn = (link_rec.part_number or "").strip()
        matched = self._filter_by_part_number(cards, target_pn)

        if not matched:
            logger.warning(
                f"{self._wid}No cards matched part number '{target_pn}' "
                f"— using all {len(cards)} cards."
            )
            matched = list(cards)

        # Apply sort order
        if self.sort_order == "high_to_low":
            matched = list(reversed(matched))

        target_cards = matched[:MAX_CARDS_PER_LINK]
        logger.info(
            f"{self._wid}Extracting data from {len(target_cards)} card(s) "
            f"(sort={self.sort_order})."
        )

        rows: list[dict] = []
        for idx, card in enumerate(target_cards):
            row = self._extract_card_data(card, link_rec, row_order=idx)
            if row:
                rows.append(row)
                logger.debug(
                    f"{self._wid}  row {idx}: price={row.get('price')}, "
                    f"unit={row.get('unit')}, contractor={row.get('contractor_name')}, "
                    f"contract#={row.get('contract_number')}"
                )

        if rows:
            insert_link_scraped_rows(self.engine, link_rec.id, link_rec.link, rows)
            logger.info(
                f"{self._wid}Saved {len(rows)} row(s) for link_id={link_rec.id}."
            )
        else:
            logger.warning(f"{self._wid}No data extracted from cards.")

        mark_imported_link_scraped(self.engine, link_rec.id)
        return len(rows) > 0

    # ── card discovery ────────────────────────────────────────────────────────

    def _wait_for_cards(self):
        """Wait up to PAGE_LOAD_TIMEOUT seconds for any card selector to appear."""
        def any_card_present(driver):
            for sel_type, sel_val in _CARD_SELECTORS:
                try:
                    if driver.find_elements(sel_type, sel_val):
                        return True
                except Exception:
                    pass
            return False

        try:
            WebDriverWait(self.driver, PAGE_LOAD_TIMEOUT).until(any_card_present)
        except TimeoutException:
            logger.warning(f"{self._wid}No card elements found within timeout.")

    def _wait_for_card_data(self):
        """Wait for Angular to populate card elements with actual price data.

        GSA Advantage card components (app-ux-product-display-inline) appear in
        the DOM as empty shells first, then Angular's async data-binding fills in
        the price/contractor text via API calls.  Calling _find_cards() immediately
        after _wait_for_cards() can therefore return cards whose .text is still
        empty.  This method polls until at least one card shows a price ($X.XX).
        """
        def cards_have_price(driver):
            for sel_type, sel_val in _CARD_SELECTORS:
                try:
                    els = driver.find_elements(sel_type, sel_val)
                    for el in els[:5]:
                        if re.search(r"\$\s*[\d,]+", el.text or ""):
                            return True
                except Exception:
                    pass
            return False

        try:
            WebDriverWait(self.driver, PAGE_LOAD_TIMEOUT).until(cards_have_price)
            logger.info(f"{self._wid}Card price data confirmed.")
        except TimeoutException:
            logger.warning(
                f"{self._wid}Card price data not confirmed within {PAGE_LOAD_TIMEOUT}s "
                "— proceeding anyway (cards may be empty)."
            )

    def _find_cards(self) -> list:
        """Return the list of card WebElements using the first matching selector."""
        for sel_type, sel_val in _CARD_SELECTORS:
            try:
                elements = self.driver.find_elements(sel_type, sel_val)
                if elements:
                    logger.debug(
                        f"{self._wid}Cards found with {sel_type}={sel_val!r}: "
                        f"{len(elements)}"
                    )
                    return elements
            except Exception:
                continue
        return []

    # ── part-number matching ──────────────────────────────────────────────────

    def _filter_by_part_number(self, cards: list, target_pn: str) -> list:
        """
        Return cards whose visible part number normalizes to the same string as
        target_pn.  If target_pn is empty, return all cards.

        Matching is done in two passes:
          1. Primary  – extract via _extract_part_number_from_card, then
                        normalize and compare.  Handles hyphens, dots, slashes.
          2. Fallback – scan the first 8 lines of raw card text and normalize
                        each line.  Catches part numbers shown with extra spaces
                        (e.g. "AFL3 1TB" should match imported "AFL3-1TB") that
                        the primary extractor's regex would otherwise skip.
        """
        if not target_pn:
            return list(cards)

        norm_target = _normalize_part_number(target_pn)
        if not norm_target:
            return list(cards)

        matched = []
        for card in cards:
            # ── Pass 1: dedicated extractor ───────────────────────────────────
            card_pn = self._extract_part_number_from_card(card)
            if card_pn and _normalize_part_number(card_pn) == norm_target:
                matched.append(card)
                continue

            # ── Pass 2: raw text scan (handles spaces + any special chars) ────
            try:
                for line in card.text.splitlines()[:8]:
                    line = line.strip()
                    if line and _normalize_part_number(line) == norm_target:
                        matched.append(card)
                        break
            except Exception:
                pass

        return matched

    def _extract_part_number_from_card(self, card) -> str:
        """Try multiple strategies to get the part number shown on a card."""
        # Strategy 1: link whose href contains 'partnumber' or 'partNumber'
        try:
            pn_links = card.find_elements(
                By.CSS_SELECTOR, "a[href*='partnumber'], a[href*='partNumber']"
            )
            for link in pn_links:
                txt = link.text.strip()
                if txt:
                    return txt
                href = link.get_attribute("href") or ""
                m = re.search(r"[pP]art[nN]umber=([^&]+)", href)
                if m:
                    return m.group(1)
        except Exception:
            pass

        # Strategy 2: first few lines of card text that look like a part number
        try:
            text = card.text
            for line in text.splitlines()[:5]:
                line = line.strip()
                if line and _RE_PART_NUMBER_LINE.match(line):
                    return line
        except Exception:
            pass

        return ""

    # ── per-card data extraction ───────────────────────────────────────────────

    def _extract_card_data(
        self, card, link_rec: ImportedLink, row_order: int
    ) -> Optional[dict]:
        """Extract price, unit, manufacturer name, contractor name and contract # from a card."""
        try:
            text = card.text

            price = self._parse_price(text)
            unit = self._parse_unit(text)
            # DOM-targeted manufacturer extraction first (most reliable),
            # then fall back to text parsing
            mfr_name = self._extract_manufacturer_dom(card) or self._parse_manufacturer(text)
            contractor_name = self._parse_contractor_name(text)
            contract_number = self._parse_contract_number_dom(card) or self._parse_contract_number_text(text)
            product_title = self._parse_product_title(card, text, link_rec.part_number)
            product_name = self._extract_product_name_dom(card)

            # Extract the actual part number shown on this specific card.
            # This lets the export layer compare it against the imported part number
            # and compute the "Part Variation" (Same / Different).
            card_part_number = self._extract_part_number_from_card(card) or link_rec.part_number

            return {
                "manufacturer_part_name": mfr_name or product_title,
                # Store the card's own part number (not the imported one) so the
                # export can detect Same vs Different variations per slot.
                "manufacturer_part_number": card_part_number,
                "product_name": product_name,
                "price": price,
                "unit": unit,
                "contractor_name": contractor_name,
                "contract_number": contract_number,
                "row_order": row_order,
            }
        except Exception as exc:
            logger.warning(f"{self._wid}Error extracting card data: {exc}")
            return None

    # ── field parsers ─────────────────────────────────────────────────────────

    def _extract_product_name_dom(self, card) -> Optional[str]:
        """
        Extract the product title shown on the card.

        GSA Advantage renders item names as:
            <span class="itemName" aria-label="Item Name - Tower NAS 2 bay ARM">
              <a ...><b><span>Tower NAS 2 bay ARM</span></b></a>
            </span>

        We try the inner <b><span> text first, then the aria-label, then the
        raw span text as a final fallback.
        """
        try:
            el = card.find_element(By.CSS_SELECTOR, "span.itemName a b span")
            txt = el.text.strip()
            if txt:
                return txt
        except Exception:
            pass
        try:
            el = card.find_element(By.CSS_SELECTOR, "span.itemName")
            aria = (el.get_attribute("aria-label") or "").strip()
            if aria:
                m = re.match(r"Item\s+Name\s*[-–]\s*(.+)", aria, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
            txt = el.text.strip()
            if txt:
                return txt
        except Exception:
            pass
        return None

    def _extract_manufacturer_dom(self, card) -> Optional[str]:
        """
        Extract manufacturer name from the card's DOM.

        GSA Advantage renders manufacturer names as:
            <div class="mfrName" aria-label="Manufacturer:QNAP INC"> Mfr: QNAP INC </div>

        We prefer the aria-label attribute (clean, no prefix to strip) and fall
        back to the element's text content with the "Mfr:" prefix removed.
        """
        try:
            # Primary: div.mfrName with aria-label="Manufacturer:..."
            els = card.find_elements(By.CSS_SELECTOR, "div.mfrName, [class*='mfrName']")
            for el in els:
                # Try aria-label first — format is "Manufacturer:QNAP INC"
                aria = (el.get_attribute("aria-label") or "").strip()
                if aria:
                    m = re.match(r"Manufacturer:\s*(.+)", aria, re.IGNORECASE)
                    if m:
                        return m.group(1).strip()
                # Fall back to visible text — format is " Mfr: QNAP INC "
                txt = el.text.strip()
                if txt:
                    cleaned = re.sub(r"^Mfr:\s*", "", txt, flags=re.IGNORECASE).strip()
                    if cleaned:
                        return cleaned
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        m = _RE_PRICE.search(text)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass
        return None

    @staticmethod
    def _parse_unit(text: str) -> Optional[str]:
        """Extract the unit abbreviation that immediately follows the price."""
        for line in text.splitlines():
            if "$" in line:
                m = _RE_UNIT_INLINE.search(line)
                if m:
                    return m.group(1)
        return None

    @staticmethod
    def _parse_manufacturer(text: str) -> Optional[str]:
        m = _RE_MFR.search(text)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def _parse_contractor_name(text: str) -> Optional[str]:
        m = _RE_CONTRACTOR.search(text)
        if m:
            name = m.group(1).strip()
            # Reject if it looks like a contract number (all uppercase + digits)
            if name and not re.match(r"^[A-Z0-9]{8,}$", name):
                return name
        return None

    @staticmethod
    def _parse_contract_number_dom(card) -> Optional[str]:
        """Extract contract number from a link whose href contains contractNumber=..."""
        try:
            links = card.find_elements(By.CSS_SELECTOR, "a[href*='contractNumber']")
            for link in links:
                href = link.get_attribute("href") or ""
                m = _RE_CONTRACT_HREF.search(href)
                if m:
                    return m.group(1)
                # Fallback: link text that looks like a contract number
                txt = link.text.strip()
                if txt and re.match(r"^[A-Z0-9\-]{6,}$", txt, re.IGNORECASE):
                    return txt
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_contract_number_text(text: str) -> Optional[str]:
        m = _RE_CONTRACT_NUM_TEXT.search(text)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def _parse_product_title(card, text: str, part_number: Optional[str]) -> Optional[str]:
        """
        Attempt to extract the product title/name from the card.
        Usually the second non-part-number line in the card text.
        """
        norm_pn = _normalize_part_number(part_number or "")
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        for line in lines[:6]:
            # Skip lines that look like part numbers
            if norm_pn and _normalize_part_number(line) == norm_pn:
                continue
            if _RE_PART_NUMBER_LINE.match(line):
                continue
            # Skip lines that start with keywords
            lower = line.lower()
            if any(lower.startswith(kw) for kw in ("mfr:", "contractor:", "contract", "$", "from ", "add to")):
                continue
            if len(line) >= 5:
                return line
        return None
