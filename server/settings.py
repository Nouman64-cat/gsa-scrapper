"""
Central configuration for the GSA Scraping Automation server.

All tuneable constants, file paths, and environment-driven settings live here.
Import from this module instead of scattering os.getenv / hardcoded values
across the codebase.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Database ──────────────────────────────────────────────────────────────────
DB_HOST = os.getenv("POSTGRESQL_HOST", "localhost")
DB_PORT = os.getenv("POSTGRESQL_PORT", "5432")
DB_NAME = os.getenv("POSTGRESQL_DATABASE", "gsa_data")
DB_USER = os.getenv("POSTGRESQL_USERNAME", "postgres")
DB_PASSWORD = os.getenv("POSTGRESQL_PASSWORD")   # No fallback – must be set in .env

# ── API / CORS ─────────────────────────────────────────────────────────────────
# Comma-separated list of allowed origins, e.g. "http://localhost:3000,https://myapp.com"
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:4000").split(",")
    if o.strip()
]

# ── File paths ────────────────────────────────────────────────────────────────
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))

EXCEL_FILE_PATH = os.path.join(SERVER_DIR, "data", "GSA Advantage Low price.xlsx")

# ── Scraping timing ───────────────────────────────────────────────────────────
SCRAPE_DELAY_SECONDS: int = int(os.getenv("SCRAPE_DELAY_SECONDS", "6"))
PAGE_LOAD_TIMEOUT: int = int(os.getenv("PAGE_LOAD_TIMEOUT", "15"))

# ── Parallel scraping ────────────────────────────────────────────────────────
# 0 = auto-detect: min(3, cpu_count // 2), each Chrome instance ~300-500 MB RAM
SCRAPE_NUM_WORKERS: int = int(os.getenv("SCRAPE_NUM_WORKERS", "0"))
SCRAPE_MAX_WORKERS: int = int(os.getenv("SCRAPE_MAX_WORKERS", "10"))
# Auto-calculated from SCRAPE_DELAY_SECONDS: 1 request per delay interval
# GSA blocks if requests arrive faster than SCRAPE_DELAY_SECONDS apart from the same IP
SCRAPE_MAX_REQUESTS_PER_MINUTE: int = 60 // SCRAPE_DELAY_SECONDS  # 6s delay → 10 req/min
SCRAPE_WORKER_MAX_RETRIES: int = int(os.getenv("SCRAPE_WORKER_MAX_RETRIES", "3"))

# ── Proxy support ───────────────────────────────────────────────────────────
# Format: ip:port:user:pass,ip:port:user:pass,...
# Each worker gets its own proxy → own IP → independent rate limiting
SCRAPE_PROXIES: list[dict] = []
_raw_proxies = os.getenv("SCRAPE_PROXIES", "").strip()
if _raw_proxies:
    for _entry in _raw_proxies.split(","):
        _entry = _entry.strip()
        if not _entry:
            continue
        _parts = _entry.split(":")
        if len(_parts) == 4:
            SCRAPE_PROXIES.append({
                "host": _parts[0],
                "port": int(_parts[1]),
                "user": _parts[2],
                "pass": _parts[3],
            })
        elif len(_parts) == 2:
            # No auth: ip:port
            SCRAPE_PROXIES.append({
                "host": _parts[0],
                "port": int(_parts[1]),
                "user": None,
                "pass": None,
            })
