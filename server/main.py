import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlmodel import SQLModel

from settings import ALLOWED_ORIGINS
from routes import imports, jobs, links, scraping, status
from database.db import get_engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run DB migrations on startup."""
    try:
        engine = get_engine()
        SQLModel.metadata.create_all(engine)
        with engine.connect() as conn:
            # Remove is_product_detail from gsa_links if it exists (moved to imported_links table)
            conn.execute(text(
                "ALTER TABLE gsa_links DROP COLUMN IF EXISTS is_product_detail"
            ))
            # Add new columns to imported_links if they don't exist
            conn.execute(text(
                "ALTER TABLE imported_links ADD COLUMN IF NOT EXISTS part_number VARCHAR DEFAULT NULL"
            ))
            conn.execute(text(
                "ALTER TABLE imported_links ADD COLUMN IF NOT EXISTS link_type VARCHAR DEFAULT 'internal'"
            ))
            # Track whether a product_detail link has already been scraped
            conn.execute(text(
                "ALTER TABLE imported_links ADD COLUMN IF NOT EXISTS is_scraped BOOLEAN DEFAULT FALSE"
            ))
            # Product name scraped from the itemName span on search result cards
            conn.execute(text(
                "ALTER TABLE links_scraped_data ADD COLUMN IF NOT EXISTS product_name VARCHAR DEFAULT NULL"
            ))
            conn.commit()
        logger.info("Database migrations completed successfully.")
    except Exception as e:
        logger.warning(f"Database migration skipped (will retry on first request): {e}")
    yield

app = FastAPI(
    title="GSA Scraper Automation API",
    description="API to run and track the GSA Advantage link generation, scraping, and export processes.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(imports.router)
app.include_router(jobs.router)
app.include_router(links.router)
app.include_router(scraping.router)
app.include_router(status.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=9000, reload=True)
