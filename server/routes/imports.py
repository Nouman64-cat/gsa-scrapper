import logging
from io import BytesIO

import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from sqlmodel import SQLModel

import settings
import state
from database.db import get_engine
from database.repository import (
    bulk_insert_imported_links,
    bulk_insert_imported_parts,
    clear_gsa_links,
    clear_gsa_scraped_data,
    clear_imported_links,
    clear_imported_parts,
    clear_links_scraped_data,
    create_job,
    get_imported_links_count,
    get_imported_parts_count,
    update_job_input_s3,
)

router = APIRouter(prefix="/api", tags=["Import"])
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = (".xlsx", ".xls")


def _link_rows_from_dataframe(df: pd.DataFrame) -> list[dict]:
    """
    Map columns (case-insensitive) and build imported_links row dicts from one sheet.

    Handles:
    - One or more "Internal Link URL" columns (including numbered variants such as
      "Internal Link URL.1", "Internal Link URL 2", "Internal Link URL_3", etc.).
      Every distinct URL found across all matching columns is added as its own row.
    - "External Link URL" + "Manufacturer Part Number" (single column pair).
    """
    internal_link_cols: list[str] = []
    external_link_col: str | None = None
    part_number_col: str | None = None

    for col in df.columns:
        lower = str(col).strip().lower()
        # Match "internal link url" and any suffixed variant (.1, _2, " 2", etc.)
        if lower == "internal link url" or lower.startswith("internal link url"):
            internal_link_cols.append(col)
        elif lower == "external link url":
            external_link_col = col
        elif lower == "manufacturer part number":
            part_number_col = col

    has_internal = len(internal_link_cols) > 0
    has_external = external_link_col is not None and part_number_col is not None

    if not has_internal and not has_external:
        return []

    records: list[dict] = []
    seen_links: set[str] = set()  # deduplicate across columns/rows

    # Process all internal link URL columns
    if has_internal:
        for int_col in internal_link_cols:
            for _, row in df.iterrows():
                link = str(row[int_col]).strip()
                if not link or link.lower() == "nan":
                    continue
                if link in seen_links:
                    continue
                seen_links.add(link)
                is_pd = "product_detail" in link.lower()
                records.append({
                    "link": link,
                    "part_number": None,
                    "is_product_detail": is_pd,
                    "link_type": "internal",
                })

    # Process external links
    if has_external:
        for _, row in df.iterrows():
            link = str(row[external_link_col]).strip()
            pn = str(row[part_number_col]).strip()
            if not link or link.lower() == "nan":
                continue
            if link in seen_links:
                continue
            seen_links.add(link)
            if not pn or pn.lower() == "nan":
                pn = None
            is_pd = "product_detail" in link.lower()
            records.append({
                "link": link,
                "part_number": pn,
                "is_product_detail": is_pd,
                "link_type": "external",
            })

    return records


@router.post("/import")
async def import_excel(file: UploadFile = File(...)):
    """Upload an Excel file to replace the current imported_parts data."""
    if not file.filename or not file.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(400, "File must be an Excel file (.xlsx or .xls)")

    contents = await file.read()
    try:
        df = pd.read_excel(BytesIO(contents))
    except Exception as e:
        raise HTTPException(422, f"Could not read Excel file: {e}")

    # Map columns (case-insensitive)
    col_map: dict[str, str] = {}
    for col in df.columns:
        lower = col.strip().lower()
        if lower == "part_number":
            col_map["part_number"] = col
        elif lower == "manufacturer":
            col_map["manufacturer"] = col

    if "part_number" not in col_map:
        raise HTTPException(
            422,
            f"Excel must have a 'part_number' column. Found: {list(df.columns)}",
        )

    # Build records
    records: list[dict] = []
    for _, row in df.iterrows():
        pn = str(row[col_map["part_number"]]).strip()
        if not pn or pn.lower() == "nan":
            continue
        mfr = ""
        if "manufacturer" in col_map:
            mfr = str(row[col_map["manufacturer"]]).strip()
            if mfr.lower() == "nan":
                mfr = ""
        records.append({"part_number": pn, "manufacturer": mfr})

    if not records:
        raise HTTPException(422, "No valid rows found in the Excel file.")

    # Store in DB (replace previous import + clear stale link/scrape data)
    engine = get_engine()
    SQLModel.metadata.create_all(engine)  # ensure table exists
    clear_imported_parts(engine)
    clear_gsa_links(engine)
    clear_gsa_scraped_data(engine)
    count = bulk_insert_imported_parts(engine, records)
    logger.info(f"Imported {count} parts from {file.filename} (old links/scraped data cleared)")

    job = create_job(engine, "parts", file.filename, count)
    with state.state_lock:
        state.current_parts_job_id = job.id

    if settings.AWS_S3_BUCKET_NAME and settings.AWS_ACCESS_KEY_ID:
        try:
            from services.aws_service import upload_input_to_s3
            s3_key = upload_input_to_s3(contents, file.filename)
            update_job_input_s3(engine, job.id, s3_key)
        except Exception:
            logger.exception("Failed to upload input file to S3 — job created without input key")

    return {
        "status": "success",
        "filename": file.filename,
        "rows_imported": count,
        "job_id": job.id,
    }


@router.post("/import/links")
async def import_links(file: UploadFile = File(...)):
    """
    Upload an Excel file to import links. Accepts:

    - **Single sheet**: Internal Link URL column and/or Manufacturer Part Number +
      External Link URL (same as before).

    - **Two-tab workbook**: First sheet — internal links; second sheet — manufacturer
      part number + external link. Columns per sheet use the same headers as the
      one-sheet format.
    """
    if not file.filename or not file.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(400, "File must be an Excel file (.xlsx or .xls)")

    contents = await file.read()
    records: list[dict] = []

    try:
        xls = pd.ExcelFile(BytesIO(contents))
    except Exception as e:
        raise HTTPException(422, f"Could not read Excel file: {e}")

    for idx, sheet in enumerate(xls.sheet_names):
        try:
            df = pd.read_excel(xls, sheet_name=sheet)
        except Exception as e:
            raise HTTPException(422, f"Could not read sheet {sheet!r}: {e}")
        part = _link_rows_from_dataframe(df)
        logger.info(
            "Links import sheet %s/%s (%r): %s row(s) parsed",
            idx + 1,
            len(xls.sheet_names),
            sheet,
            len(part),
        )
        records.extend(part)

    if not records:
        sample_cols = []
        try:
            df0 = pd.read_excel(xls, sheet_name=0)
            sample_cols = list(df0.columns)
        except Exception:
            pass
        raise HTTPException(
            422,
            "No valid link rows found. Each sheet must have either an "
            "'Internal Link URL' column, or both 'Manufacturer Part Number' and "
            f"'External Link URL' columns. First sheet columns: {sample_cols}",
        )

    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    clear_links_scraped_data(engine)   # wipe previous session's scraped results
    clear_imported_links(engine)
    count = bulk_insert_imported_links(engine, records)

    product_detail_count = sum(1 for r in records if r["is_product_detail"])
    search_count = count - product_detail_count
    internal_count = sum(1 for r in records if r["link_type"] == "internal")
    external_count = count - internal_count
    logger.info(
        f"Imported {count} links from {file.filename} "
        f"({internal_count} internal, {external_count} external, "
        f"{product_detail_count} product_detail, {search_count} advantage_search)"
    )

    job = create_job(engine, "links", file.filename, count)
    with state.state_lock:
        state.current_links_job_id = job.id

    if settings.AWS_S3_BUCKET_NAME and settings.AWS_ACCESS_KEY_ID:
        try:
            from services.aws_service import upload_input_to_s3
            s3_key = upload_input_to_s3(contents, file.filename)
            update_job_input_s3(engine, job.id, s3_key)
        except Exception:
            logger.exception("Failed to upload input file to S3 — job created without input key")

    return {
        "status": "success",
        "filename": file.filename,
        "rows_imported": count,
        "internal_links": internal_count,
        "external_links": external_count,
        "product_detail_links": product_detail_count,
        "search_links": search_count,
        "job_id": job.id,
    }


@router.get("/import/status")
async def import_status():
    """Return counts for imported parts and imported links."""
    engine = get_engine()
    parts_count = get_imported_parts_count(engine)
    links_count = get_imported_links_count(engine)

    # Count product_detail vs search links
    from database.models import ImportedLink
    from sqlmodel import Session, select
    with Session(engine) as session:
        product_detail_count = len(
            session.exec(select(ImportedLink).where(ImportedLink.is_product_detail == True)).all()
        )
    search_count = links_count - product_detail_count

    return {
        "imported_parts_count": parts_count,
        "imported_links_count": links_count,
        "product_detail_count": product_detail_count,
        "search_count": search_count,
    }
