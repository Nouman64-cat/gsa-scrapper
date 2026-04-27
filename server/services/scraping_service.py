import pandas as pd
import time
import re
import os
import shutil
import random
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from dotenv import load_dotenv
import sys
import logging
import yaml
from sqlmodel import Session, SQLModel, select

# Ensure the root project dir is in sys.path so we can import models.py
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from database.models import GSALink, GSAScrapedData
from database.db import get_engine
from database.repository import get_link_by_part_number, mark_link_scraped, upsert_scraped_data, get_all_imported_parts
from settings import SCRAPE_DELAY_SECONDS, PAGE_LOAD_TIMEOUT, EXCEL_FILE_PATH
from services.manufacturer_normalizer import ManufacturerNormalizer
from services.proxy_auth import create_proxy_auth_extension

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

EXCEL_FILE = EXCEL_FILE_PATH


class GSAScrapingAutomation:
    def __init__(self, excel_file_path, stop_event=None, rate_limiter=None,
                 on_row_complete=None, worker_id=None, proxy=None, proxies=None,
                 headless=True):
        self.excel_file_path = excel_file_path
        self.driver = None
        self.wait = None
        self._normalizer = ManufacturerNormalizer()
        self._compile_regex_patterns()
        self.engine = None
        self._setup_db()
        # Parallel-aware stop: shared threading.Event or fallback to boolean
        self._stop_event = stop_event
        self._stop_flag = False
        self._rate_limiter = rate_limiter
        self._on_row_complete = on_row_complete
        self._worker_id = worker_id
        self._proxy = proxy  # {"host", "port", "user", "pass"} or None
        self._proxies = proxies
        self._proxy_ext_path = None  # temp file cleanup
        self._headless = headless

    @property
    def stop_requested(self):
        if self._stop_event is not None:
            return self._stop_event.is_set()
        return self._stop_flag

    def stop(self):
        """Signal the automation to stop as soon as possible"""
        if self._stop_event is not None:
            self._stop_event.set()
        else:
            self._stop_flag = True
        logger.info("Stop signal received. Finishing current task...")

    def _setup_db(self):
        """Initialize database connection"""
        try:
            self.engine = get_engine()
            SQLModel.metadata.create_all(self.engine)
            logger.info("Database connection setup successfully.")
        except Exception as e:
            logger.error(f"Failed to setup database: {str(e)}")
            self.engine = None

    def _compile_regex_patterns(self):
        """Pre-compile regex patterns for better performance"""
        # Price patterns
        self._price_patterns = [
            re.compile(r'\$\s*([\d,]+\.?\d*)', re.IGNORECASE),
            re.compile(r'([\d,]+\.\d{2})\s*EA', re.IGNORECASE),
            re.compile(r'([\d,]+\.\d{2})\s*USD', re.IGNORECASE),
            re.compile(r'price[:\s]*\$?\s*([\d,]+\.?\d*)', re.IGNORECASE),
            re.compile(r'unit[:\s]*\$?\s*([\d,]+\.?\d*)', re.IGNORECASE),
            re.compile(r'each[:\s]*\$?\s*([\d,]+\.?\d*)', re.IGNORECASE),
        ]

        # Contractor patterns
        self._contractor_patterns = [
            re.compile(r'contractor[:\s]*\n([^\n]+?)(?:\n|contract#|Contract#|includes)', re.IGNORECASE | re.MULTILINE),
            re.compile(r'contractor[:\s]*([^\n]+?)(?:\n|contract#|Contract#|includes)', re.IGNORECASE | re.MULTILINE),
            re.compile(r'vendor[:\s]*\n([^\n]+?)(?:\n|contract#|Contract#|includes)', re.IGNORECASE | re.MULTILINE),
            re.compile(r'supplier[:\s]*\n([^\n]+?)(?:\n|contract#|Contract#|includes)', re.IGNORECASE | re.MULTILINE),
            re.compile(r'company[:\s]*\n([^\n]+?)(?:\n|contract#|Contract#|includes)', re.IGNORECASE | re.MULTILINE),
            re.compile(r'distributor[:\s]*\n([^\n]+?)(?:\n|contract#|Contract#|includes)', re.IGNORECASE | re.MULTILINE),
        ]

        # Manufacturer patterns
        self._manufacturer_patterns = [
            re.compile(r'\bmfr[:\s]*([a-z0-9\s&.,®\-]+)', re.IGNORECASE),
            re.compile(r'\bmanufacturer[:\s]*([a-z0-9\s&.,®\-]+)', re.IGNORECASE),
            re.compile(r'\bmfg[:\s]*([a-z0-9\s&.,®\-]+)', re.IGNORECASE),
            re.compile(r'\bbrand[:\s]*([a-z0-9\s&.,®\-]+)', re.IGNORECASE)
        ]

        # Unit patterns - used as fallback only; primary extraction is line-based
        # Pattern: price/unit with slash e.g. "$80.00/EA"
        self._unit_slash_pattern = re.compile(
            r'\$\s*[\d,]+\.?\d*\s*/\s*([A-Za-z]{1,4})\b', re.IGNORECASE
        )
        # Pattern: strict labeled UOM field e.g. "uom: EA"
        self._unit_uom_pattern = re.compile(
            r'\buom\b\s*:\s*([A-Za-z]{1,4})\b', re.IGNORECASE
        )
        # Pattern: strict labeled unit field with colon e.g. "unit: EA"
        # Requires colon to avoid matching "united", "unit size", "unit price"
        self._unit_label_pattern = re.compile(
            r'\bunit\s*:\s*([A-Za-z]{1,4})\b', re.IGNORECASE
        )
        # Pattern: price followed immediately by a short abbreviation on the same line
        # e.g. "$ 80.00 EA" — the primary GSA Advantage display format
        self._unit_price_inline_pattern = re.compile(
            r'\$\s*[\d,]+\.?\d*\s+([A-Za-z]{2,4})\b'
        )

    def setup_driver(self):
        """Initialize Chrome driver with optimized options for speed"""
        chrome_options = Options()

        if self._headless:
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--window-size=1920,1080")

        # User-Agent rotation for stealth
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:123.0) Gecko/20100101 Firefox/123.0"
        ]
        chosen_ua = random.choice(user_agents)
        chrome_options.add_argument(f"user-agent={chosen_ua}")
        
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--disable-plugins")
        chrome_options.add_argument("--disable-images")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--disable-features=VizDisplayCompositor")
        chrome_options.add_argument("--disable-background-timer-throttling")
        chrome_options.add_argument("--disable-backgrounding-occluded-windows")
        chrome_options.add_argument("--disable-renderer-backgrounding")
        chrome_options.add_argument("--disable-background-networking")
        chrome_options.add_argument("--disable-sync")
        chrome_options.add_argument("--disable-translate")
        chrome_options.add_argument("--disable-ipc-flooding-protection")
        chrome_options.add_argument("--memory-pressure-off")
        chrome_options.add_argument("--max_old_space_size=4096")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_experimental_option("prefs", {
            "profile.default_content_setting_values": {
                "images": 2,
                "plugins": 2,
                "popups": 2,
                "geolocation": 2,
                "notifications": 2,
                "media_stream": 2,
            }
        })

        # Proxy configuration
        active_proxy = None
        if self._proxies:
            active_proxy = random.choice(self._proxies)
        elif self._proxy:
            active_proxy = self._proxy

        if active_proxy:
            proxy = active_proxy
            if proxy.get("user"):
                # Authenticated proxy → needs Chrome extension
                # NOTE: --disable-extensions must NOT be set for this to work
                self._proxy_ext_path = create_proxy_auth_extension(
                    proxy["host"], proxy["port"], proxy["user"], proxy["pass"]
                )
                chrome_options.add_extension(self._proxy_ext_path)
                logger.info(f"[W{self._worker_id}] Using proxy: {proxy['host']}:{proxy['port']} (authenticated)")
            else:
                # Unauthenticated proxy → simple flag
                chrome_options.add_argument("--disable-extensions")
                chrome_options.add_argument(f"--proxy-server=http://{proxy['host']}:{proxy['port']}")
                logger.info(f"[W{self._worker_id}] Using proxy: {proxy['host']}:{proxy['port']}")
        else:
            chrome_options.add_argument("--disable-extensions")

        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self.wait = WebDriverWait(self.driver, PAGE_LOAD_TIMEOUT)

    def fuzzy_match_manufacturer(self, original_manufacturer, website_manufacturer, threshold=0.85):
        """Delegate manufacturer matching to ManufacturerNormalizer."""
        return self._normalizer.matches(original_manufacturer, website_manufacturer, threshold)

    def read_excel_data(self):
        """Read Excel file with GSA links"""
        try:
            df = pd.read_excel(self.excel_file_path)
            logger.info(f"Excel file loaded. Columns: {list(df.columns)}")

            # Find required columns
            column_mapping = {'manufacturer': None, 'part_number': None}

            for col in df.columns:
                col_lower = col.strip().lower()
                if col_lower == 'manufacturer':
                    column_mapping['manufacturer'] = col
                elif col_lower == 'part_number':
                    column_mapping['part_number'] = col

            missing = [k for k, v in column_mapping.items() if v is None]
            if missing:
                logger.error(f"Missing required columns: {missing}")
                return None, None

            # Ensure output columns exist and are string-compatible
            output_cols = [
                '1 GSA Low Price', 'Unit', 'Contractor:Name',
                '2 GSA Low Price', 'Unit.1', 'Contractor:Name.1'
            ]
            for col in output_cols:
                if col not in df.columns:
                    df[col] = ''
                else:
                    df[col] = df[col].astype(object)

            logger.info(f"Found {len(df)} rows to process")
            return df, column_mapping

        except Exception as e:
            logger.error(f"Error reading Excel file: {str(e)}")
            return None, None

    def read_data(self):
        """Return (df, column_mapping) from imported_parts DB."""
        if self.engine:
            records = get_all_imported_parts(self.engine)
            if records:
                rows = [
                    {"manufacturer": r.manufacturer or "", "part_number": r.part_number}
                    for r in records
                    if r.part_number
                ]
                df = pd.DataFrame(rows)
                column_mapping = {"manufacturer": "manufacturer", "part_number": "part_number"}
                logger.info(f"Loaded {len(df)} rows from imported_parts DB")
                return df, column_mapping

        logger.warning("No imported parts found. Please import an Excel file first.")
        return None, None

    def scrape_gsa_page(self, gsa_url, target_manufacturer):
        """Scrape GSA page and return top 2 products matching the manufacturer"""
        try:
            if not self.driver:
                logger.warning("Driver is not initialized. Setting up driver...")
                self.setup_driver()

            # Health check: gracefully recover if the browser was closed or crashed
            try:
                _ = self.driver.title
            except Exception:
                logger.warning("WebDriver is detached or crashed. Re-initializing...")
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self.setup_driver()

            logger.info(f"Scraping: {gsa_url}")
            self.driver.get(gsa_url)

            # Wait for page load
            try:
                WebDriverWait(self.driver, 8).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )
            except TimeoutException:
                logger.warning("Page readyState timeout, continuing anyway")

            time.sleep(2)

            # Wait for product elements
            def any_product_present(driver):
                for sel_type, sel_val in [
                    (By.CSS_SELECTOR, ".productViewControl"),
                    (By.CSS_SELECTOR, "app-ux-product-display-inline"),
                    (By.CSS_SELECTOR, ".product-item"),
                    (By.CSS_SELECTOR, ".result-item"),
                ]:
                    try:
                        if driver.find_elements(sel_type, sel_val):
                            return True
                    except Exception:
                        continue
                return False

            try:
                WebDriverWait(self.driver, 5).until(any_product_present)
                
                # Human-like behavioral noise: random scroll and pause
                try:
                    scroll_y = random.randint(150, 400)
                    self.driver.execute_script(f"window.scrollBy(0, {scroll_y});")
                    time.sleep(random.uniform(0.4, 1.2))
                    self.driver.execute_script(f"window.scrollBy(0, -{random.randint(50, scroll_y)} );")
                    time.sleep(random.uniform(0.3, 0.8))
                except Exception:
                    pass
            except TimeoutException:
                logger.warning("No product elements found within 10 seconds")

            # First pass without scrolling
            products = self._find_product_elements()
            if not products:
                logger.warning(f"No products found: {gsa_url}")
                # Check if it might be an error page/captcha/block
                if self.driver:
                    page_text = self.driver.page_source.lower()
                    error_keywords = ["access denied", "incident number", "captcha", "security check", "forbidden", "system error", "unexpected error", "502 bad gateway", "503 service unavailable"]
                    if any(err in page_text for err in error_keywords):
                        logger.error("GSA error page or block detected! Forcing browser restart.")
                        try:
                            self.driver.quit()
                        except Exception:
                            pass
                        self.driver = None
                return []

            initial_matches = self._extract_and_filter_products(products, target_manufacturer)

            if len(initial_matches) >= 2:
                return initial_matches[:2]

            # Smart scroll if not enough matches
            if len(initial_matches) > 0:
                self._smart_scroll_to_load_more_products()
                products = self._find_product_elements()
                final_matches = self._extract_and_filter_products(products, target_manufacturer)
                return final_matches[:2]
            else:
                self._scroll_to_load_all_products()
                products = self._find_product_elements()
                final_matches = self._extract_and_filter_products(products, target_manufacturer)
                return final_matches[:2]

        except Exception as e:
            logger.error(f"Error scraping {gsa_url}: {str(e)}")
            # Force browser to restart on the next iteration if an unhandled error occurred
            try:
                if self.driver:
                    self.driver.quit()
            except Exception:
                pass
            self.driver = None
            return []

    def _smart_scroll_to_load_more_products(self):
        """Smart scroll - load more products"""
        try:
            last_height = self.driver.execute_script("return document.body.scrollHeight")
            for _ in range(5):
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                new_height = self.driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)
        except Exception as e:
            logger.warning(f"Error during smart scrolling: {str(e)}")

    def _scroll_to_load_all_products(self):
        """Full scroll to load all products"""
        try:
            last_height = self.driver.execute_script("return document.body.scrollHeight")
            for _ in range(8):
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                new_height = self.driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)
        except Exception as e:
            logger.warning(f"Error during full scrolling: {str(e)}")

    def _extract_and_filter_products(self, products, target_manufacturer):
        """Extract product info and filter by manufacturer match only"""
        # Skip header row if present
        start_index = 0
        if products:
            first_text = products[0].text.lower()
            if any(h in first_text for h in ['name contract number price', 'price low to high', 'view as grid', 'sort by']):
                start_index = 1

        all_products_info = []
        for i in range(start_index, len(products)):
            try:
                product_info = self._extract_product_info(products[i], i + 1, target_manufacturer)
                if product_info and product_info.get('price') is not None:
                    # Skip header-like entries
                    contractor = product_info.get('contractor', '') or ''
                    if any(h in contractor.lower() for h in ['name contract', 'price low', 'view as', 'sort by']):
                        continue
                    all_products_info.append(product_info)
            except Exception as e:
                logger.warning(f"Error extracting product {i + 1}: {str(e)}")

        # Filter by manufacturer match only
        matching = []
        for product in all_products_info:
            if product.get('manufacturer_match', False):
                matching.append(product)
                logger.info(f"MATCHED Product {product['product_num']}: Price={product['price']}, "
                            f"Unit={product.get('unit')}, Contractor={product['contractor']}, "
                            f"Mfr={product.get('website_manufacturer')}")
            else:
                logger.debug(f"REJECTED Product {product['product_num']}: "
                             f"Mfr mismatch (target='{target_manufacturer}', "
                             f"found='{product.get('website_manufacturer')}')")

        return matching

    def _find_product_elements(self):
        """Find product elements on the GSA page"""
        product_selectors = [
            (By.CSS_SELECTOR, ".productViewControl"),
            (By.CSS_SELECTOR, "app-ux-product-display-inline"),
            (By.CSS_SELECTOR, ".product-item"),
            (By.CSS_SELECTOR, ".result-item"),
            (By.CSS_SELECTOR, ".product"),
            (By.CSS_SELECTOR, "[class*='product']"),
            (By.CSS_SELECTOR, "[class*='result']"),
            (By.XPATH, "//div[contains(@class, 'product')]"),
            (By.XPATH, "//div[contains(@class, 'result')]"),
            (By.XPATH, "//div[contains(@class, 'item')]"),
            (By.XPATH, "//tr[contains(@class, 'product')]"),
        ]
        for sel_type, sel_val in product_selectors:
            try:
                elements = self.driver.find_elements(sel_type, sel_val)
                if elements:
                    logger.info(f"Found {len(elements)} products with: {sel_type}={sel_val}")
                    return elements
            except Exception:
                continue
        logger.warning("No product elements found with any selector")
        return []

    def _extract_product_info(self, product_element, product_num, target_manufacturer):
        """Extract price, unit, contractor and manufacturer from a product element"""
        try:
            product_text = product_element.text.lower()

            price = self._extract_price(product_text)
            unit = self._extract_unit(product_text)
            contractor = self._extract_contractor(product_text)
            website_manufacturer = self._extract_manufacturer(product_text)

            manufacturer_match = self.fuzzy_match_manufacturer(target_manufacturer, website_manufacturer)

            return {
                'product_num': product_num,
                'price': price,
                'unit': unit,
                'contractor': contractor,
                'manufacturer_match': manufacturer_match,
                'website_manufacturer': website_manufacturer,
                'raw_text': product_element.text[:200]
            }

        except Exception as e:
            logger.error(f"Error extracting product info: {str(e)}")
            return None

    def _extract_price(self, text):
        """Extract price from product text"""
        for pattern in self._price_patterns:
            matches = pattern.findall(text)
            if matches:
                try:
                    return float(matches[0].replace(',', '').strip())
                except ValueError:
                    continue
        return None

    def _extract_unit(self, text):
        """Extract unit of measure from product text.

        Strategy: find the line that contains the price, then extract the
        abbreviation that immediately follows the price on that same line.
        GSA Advantage consistently shows prices as "$ 80.00 EA" where the
        unit is the only token after the price on that line.  Fall back to
        labeled UOM/unit fields if no price line is found.
        """
        price_present = re.compile(r'\$\s*[\d,]+\.?\d*')

        # --- Step 1: scan line-by-line for the price line ---
        for line in text.splitlines():
            line = line.strip()
            if not line or not price_present.search(line):
                continue

            # Try "$ 80.00 EA" — unit directly after the price
            m = self._unit_price_inline_pattern.search(line)
            if m:
                unit = m.group(1).upper()
                # Accept 2-4 char abbreviations; reject if the whole line after
                # the price is just a continuation of a longer word (word
                # boundary already guaranteed by the pattern)
                if 2 <= len(unit) <= 4:
                    return unit

            # Try "$80.00/EA" — slash-separated unit
            m = self._unit_slash_pattern.search(line)
            if m:
                unit = m.group(1).upper()
                if 1 <= len(unit) <= 4:
                    return unit

        # --- Step 2: labeled UOM/unit fields anywhere in the text ---
        m = self._unit_uom_pattern.search(text)
        if m:
            unit = m.group(1).upper()
            if 1 <= len(unit) <= 4:
                return unit

        m = self._unit_label_pattern.search(text)
        if m:
            unit = m.group(1).upper()
            if 1 <= len(unit) <= 4:
                return unit

        return None

    def _extract_contractor(self, text):
        """Extract contractor name from product text"""
        for pattern in self._contractor_patterns:
            matches = pattern.findall(text)
            if matches:
                contractor = matches[0].strip()
                contractor = re.sub(r'\s+', ' ', contractor)
                contractor = re.sub(r'\s+contract\s*$', '', contractor, flags=re.IGNORECASE)
                contractor = re.sub(r'\s+includes\s*$', '', contractor, flags=re.IGNORECASE)
                contractor = re.sub(r'\s+inc\.?\s*$', ' Inc.', contractor, flags=re.IGNORECASE)
                contractor = re.sub(r'\s+llc\s*$', ' LLC', contractor, flags=re.IGNORECASE)
                contractor = re.sub(r'\s+corp\.?\s*$', ' Corp.', contractor, flags=re.IGNORECASE)
                return contractor.title()
        return None

    def _extract_manufacturer(self, text):
        """Extract manufacturer name from product text"""
        for pattern in self._manufacturer_patterns:
            m = pattern.search(text)
            if m:
                value = m.group(1).strip()
                return re.sub(r'\s+', ' ', value)
        return None

    def save_results_to_db(self, part_number, products_data):
        """Save scraped results to PostgreSQL DB."""
        try:
            result = upsert_scraped_data(self.engine, part_number, products_data)
            logger.info(f"Saved {len(products_data)} products to DB for {part_number}")
            return result
        except Exception as e:
            logger.error(f"Error saving to DB for {part_number}: {str(e)}")
            return False

    def _get_link_from_db(self, part_number):
        """Fetch the GSALink record for part_number from the DB."""
        return get_link_by_part_number(self.engine, part_number)

    def _mark_link_scraped(self, part_number):
        """Mark a GSALink as scraped in the DB."""
        mark_link_scraped(self.engine, part_number)

    def _execute_scraping_loop(self, indices, df, column_mapping):
        """Core scraping loop - processes a list of integer DataFrame indices."""
        total = len(indices)
        successful = 0
        failed = 0
        start_time = time.time()
        wid = f"[W{self._worker_id}] " if self._worker_id is not None else ""

        for offset, i in enumerate(indices, 1):
            if self.stop_requested:
                logger.warning(f"{wid}Stop requested. Exiting loop.")
                break
            try:
                manufacturer = df.at[i, column_mapping['manufacturer']]
                part_number = df.at[i, column_mapping['part_number']]

                link_record = self._get_link_from_db(part_number)
                if not link_record or not link_record.gsa_link:
                    logger.warning(f"{wid}Row {i + 1}: No DB URL for {part_number}")
                    if self._on_row_complete:
                        self._on_row_complete(part_number, False)
                    continue
                if link_record.is_scraped:
                    logger.info(f"{wid}Row {i + 1}: Skipping {part_number} (already scraped)")
                    if self._on_row_complete:
                        self._on_row_complete(part_number, True)
                    continue

                gsa_url = link_record.gsa_link
                logger.info(f"{wid}Progress: {offset}/{total} (Row {i + 1}) - {part_number}")

                # Global rate limiting across all workers
                if self._rate_limiter:
                    self._rate_limiter.acquire()
                    # Add small jitter to rate-limited requests to avoid perfect cadence
                    jitter = random.uniform(0.5, 2.0)
                    if self._stop_event is not None:
                        self._stop_event.wait(timeout=jitter)
                    else:
                        time.sleep(jitter)

                t0 = time.time()
                products_data = self.scrape_gsa_page(gsa_url, manufacturer)
                elapsed = time.time() - t0

                success = False
                if products_data:
                    successful += 1
                    success = True
                    self.save_results_to_db(part_number, products_data)
                    logger.info(f"{wid}SUCCESS: {len(products_data)} products ({elapsed:.1f}s)")
                else:
                    failed += 1
                    logger.warning(f"{wid}No matches for {part_number} ({elapsed:.1f}s)")

                self._mark_link_scraped(part_number)

                if self._on_row_complete:
                    self._on_row_complete(part_number, success)

                total_elapsed = time.time() - start_time
                avg = total_elapsed / offset
                eta_h = (total - offset) * avg / 3600
                logger.info(f"{wid}Avg: {avg:.1f}s/row | ETA: {eta_h:.1f}h")

                # Delay between requests: rate limiter handles pacing in
                # parallel mode; standalone mode falls back to a simple sleep.
                if not self._rate_limiter:
                    actual_delay = max(float(SCRAPE_DELAY_SECONDS), SCRAPE_DELAY_SECONDS * random.uniform(1.0, 1.5))
                    if self._stop_event is not None:
                        self._stop_event.wait(timeout=actual_delay)
                    else:
                        time.sleep(actual_delay)

            except Exception as e:
                failed += 1
                logger.error(f"{wid}Error on row {i + 1}: {str(e)}")
                
                # Tear down the driver so the next iteration gets a fresh one
                try:
                    if self.driver:
                        self.driver.quit()
                except Exception:
                    pass
                self.driver = None

                if self._on_row_complete:
                    self._on_row_complete(str(i), False)

        total_time = time.time() - start_time
        logger.info(f"{wid}Done! {successful}/{total} successful, {failed} failed in {total_time / 60:.1f} min")
        return successful

    def identify_missing_rows(self, df):
        """Identify DataFrame indices where GSA scraped data is absent from the DB."""
        missing_rows = []
        with Session(self.engine) as session:
            scraped_parts = {
                row.part_number for row in session.exec(select(GSAScrapedData.part_number)).all()
            }
        for i, row in df.iterrows():
            part_number = str(row.get('Part Number') or row.get('part_number', '')).strip()
            if part_number and part_number not in scraped_parts:
                missing_rows.append(i)
        return missing_rows

    # ─────────────────────────────────────────────────────────────────
    # Run modes
    # ─────────────────────────────────────────────────────────────────

    def run_scraping_test_mode(self, item_limit=3):
        """Test with the first N rows."""
        try:
            df, column_mapping = self.read_excel_data()
            if df is None:
                return False
            self.setup_driver()
            indices = list(df.head(item_limit).index)
            self._execute_scraping_loop(indices, df, column_mapping)
            return True
        except Exception as e:
            logger.error(f"Test mode error: {str(e)}")
            return False
        finally:
            if self.driver:
                self.driver.quit()

    def run_scraping_full(self):
        """Full automation - process all rows."""
        try:
            df, column_mapping = self.read_excel_data()
            if df is None:
                return False
            self.setup_driver()
            self._execute_scraping_loop(list(df.index), df, column_mapping)
            return True
        except Exception as e:
            logger.error(f"Full run error: {str(e)}")
            return False
        finally:
            if self.driver:
                self.driver.quit()

    def run_scraping_custom_range(self, start_row: int, end_row: int):
        """Process a specific row range (1-based, inclusive)."""
        try:
            df, column_mapping = self.read_excel_data()
            if df is None:
                return False
            start_idx = max(0, start_row - 1)
            end_idx = min(len(df) - 1, end_row - 1)
            if start_idx > end_idx:
                logger.error(f"Invalid range: {start_row}-{end_row}")
                return False
            self.setup_driver()
            self._execute_scraping_loop(list(range(start_idx, end_idx + 1)), df, column_mapping)
            return True
        except Exception as e:
            logger.error(f"Custom range error: {str(e)}")
            return False
        finally:
            if self.driver:
                self.driver.quit()

    def run_scraping_missing_only(self, start_from: int = 0):
        """Scrape only rows not yet present in the DB.

        Args:
            start_from: Skip missing rows with index below this value (0 = process all).
                        When called from the CLI the caller supplies this after prompting the user.
        """
        try:
            df, column_mapping = self.read_excel_data()
            if df is None:
                return False
            missing_rows = self.identify_missing_rows(df)
            logger.info(f"Found {len(missing_rows)} rows with missing data")
            if not missing_rows:
                logger.info("All rows already have data!")
                return True
            if start_from > 0:
                missing_rows = [r for r in missing_rows if r >= start_from]
                logger.info(f"Filtered to {len(missing_rows)} missing rows from index {start_from}")
            self.setup_driver()
            self._execute_scraping_loop(missing_rows, df, column_mapping)
            return True
        except Exception as e:
            logger.error(f"Missing-only error: {str(e)}")
            return False
        finally:
            if self.driver:
                self.driver.quit()


def main():
    print("\n" + "=" * 60)
    print("GSA SCRAPING AUTOMATION")
    print("=" * 60)
    print(f"Input:        GSA Advantage Low price.xlsx")
    print(f"Match by:     Manufacturer Name")
    print(f"Output:       PostgreSQL DB (gsa_scraped_data table)")
    print("=" * 60)

    automation = GSAScrapingAutomation(EXCEL_FILE)

    while True:
        print("\nChoose automation mode:")
        print("1. Test mode (first 3 rows)")
        print("2. Full automation (all rows)")
        print("3. Custom range (specific rows)")
        print("4. Missing rows only")
        print("5. Exit")

        choice = input("\nEnter your choice (1-5): ").strip()

        if choice == '5':
            print("Goodbye!")
            break
        elif choice == '1':
            print("\nRunning test mode (first 3 rows)...")
            success = automation.run_scraping_test_mode(3)
        elif choice == '2':
            confirm = input("Process all rows? (y/n): ").strip().lower()
            if confirm in ('y', 'yes'):
                success = automation.run_scraping_full()
            else:
                print("Cancelled.")
                continue
        elif choice == '3':
            try:
                start_row = int(input("Start row (1-based): "))
                end_row = int(input("End row (1-based): "))
                if start_row < 1 or end_row < start_row:
                    print("Invalid range.")
                    continue
                success = automation.run_scraping_custom_range(start_row, end_row)
            except ValueError:
                print("Please enter valid numbers.")
                continue
        elif choice == '4':
            try:
                start_from_input = input("Start from row index (0 for all): ").strip()
                start_from = int(start_from_input) if start_from_input.isdigit() else 0
            except ValueError:
                start_from = 0
            success = automation.run_scraping_missing_only(start_from=start_from)
        else:
            print("Invalid choice.")
            continue

        if success:
            print("\nAutomation completed successfully!")
        else:
            print("\nAutomation failed. Check logs for details.")

        again = input("\nRun another? (y/n): ").strip().lower()
        if again not in ('y', 'yes'):
            print("Goodbye!")
            break


if __name__ == "__main__":
    main()
