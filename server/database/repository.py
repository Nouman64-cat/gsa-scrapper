from datetime import datetime
from typing import Optional
from sqlalchemy import delete as _sql_delete
from sqlmodel import Session, select
from database.models import GSALink, GSAScrapedData, ImportedLink, ImportedPart, Job, LinkScrapedData


def get_link_by_part_number(engine, part_number):
    """Return the GSALink record for the given part number, or None."""
    with Session(engine) as session:
        return session.exec(
            select(GSALink).where(GSALink.part_number == str(part_number))
        ).first()


def mark_link_scraped(engine, part_number):
    """Set is_scraped=True on the GSALink record for part_number."""
    with Session(engine) as session:
        rec = session.exec(
            select(GSALink).where(GSALink.part_number == str(part_number))
        ).first()
        if rec:
            rec.is_scraped = True
            session.add(rec)
            session.commit()


def upsert_link(engine, part_number, gsa_url):
    """Insert or update a GSALink record."""
    with Session(engine) as session:
        rec = session.exec(
            select(GSALink).where(GSALink.part_number == str(part_number))
        ).first()
        if rec:
            rec.gsa_link = gsa_url
            rec.created_at = datetime.utcnow()
        else:
            rec = GSALink(part_number=str(part_number), gsa_link=gsa_url)
            session.add(rec)
        session.commit()
    return True


def upsert_scraped_data(engine, part_number, products_data):
    """Insert or update a GSAScrapedData record from a list of up to 2 product dicts."""
    val_1 = products_data[0] if len(products_data) > 0 else {}
    val_2 = products_data[1] if len(products_data) > 1 else {}

    with Session(engine) as session:
        rec = session.exec(
            select(GSAScrapedData).where(GSAScrapedData.part_number == str(part_number))
        ).first()
        if rec:
            rec.gsa_low_price_1 = val_1.get('price')
            rec.unit_1 = val_1.get('unit')
            rec.contractor_1 = val_1.get('contractor')
            rec.gsa_low_price_2 = val_2.get('price')
            rec.unit_2 = val_2.get('unit')
            rec.contractor_2 = val_2.get('contractor')
            rec.created_at = datetime.utcnow()
        else:
            rec = GSAScrapedData(
                part_number=str(part_number),
                gsa_low_price_1=val_1.get('price'),
                unit_1=val_1.get('unit'),
                contractor_1=val_1.get('contractor'),
                gsa_low_price_2=val_2.get('price'),
                unit_2=val_2.get('unit'),
                contractor_2=val_2.get('contractor'),
            )
            session.add(rec)
        session.commit()
    return True


# ── Imported parts ─────────────────────────────────────────────

def clear_imported_parts(engine):
    """Delete all rows from imported_parts (preparing for a fresh import)."""
    with Session(engine) as session:
        records = session.exec(select(ImportedPart)).all()
        for rec in records:
            session.delete(rec)
        session.commit()


def bulk_insert_imported_parts(engine, parts: list[dict]) -> int:
    """Insert a list of {'part_number': ..., 'manufacturer': ...} dicts. Returns count."""
    with Session(engine) as session:
        for p in parts:
            session.add(ImportedPart(
                part_number=p["part_number"],
                manufacturer=p.get("manufacturer", ""),
            ))
        session.commit()
    return len(parts)


def get_imported_parts_count(engine) -> int:
    """Return how many rows are in imported_parts."""
    with Session(engine) as session:
        return len(session.exec(select(ImportedPart)).all())


def get_all_imported_parts(engine) -> list[ImportedPart]:
    """Return all imported part records."""
    with Session(engine) as session:
        return session.exec(select(ImportedPart).order_by(ImportedPart.id)).all()


# ── Imported links ─────────────────────────────────────────────

def clear_imported_links(engine):
    """Delete all rows from imported_links in a single atomic SQL DELETE."""
    with Session(engine) as session:
        session.exec(_sql_delete(ImportedLink))
        session.commit()


def bulk_insert_imported_links(engine, links: list[dict]) -> int:
    """Insert a list of link dicts into imported_links. Returns count."""
    with Session(engine) as session:
        for ln in links:
            session.add(ImportedLink(
                link=ln["link"],
                part_number=ln.get("part_number"),
                is_product_detail=ln.get("is_product_detail", False),
                link_type=ln.get("link_type", "internal"),
            ))
        session.commit()
    return len(links)


def get_imported_links_count(engine) -> int:
    """Return how many rows are in imported_links."""
    with Session(engine) as session:
        return len(session.exec(select(ImportedLink)).all())


def get_all_imported_links(engine) -> list[ImportedLink]:
    """Return all imported link records."""
    with Session(engine) as session:
        return session.exec(select(ImportedLink).order_by(ImportedLink.id)).all()


def clear_gsa_links(engine):
    """Delete all rows from gsa_links."""
    with Session(engine) as session:
        records = session.exec(select(GSALink)).all()
        for rec in records:
            session.delete(rec)
        session.commit()


def clear_gsa_scraped_data(engine):
    """Delete all rows from gsa_scraped_data."""
    with Session(engine) as session:
        records = session.exec(select(GSAScrapedData)).all()
        for rec in records:
            session.delete(rec)
        session.commit()


# ── Links scraped data (product_detail compare sources) ────────

def get_all_product_detail_links(engine) -> list[ImportedLink]:
    """Return all ImportedLink rows where is_product_detail=True and not yet scraped."""
    with Session(engine) as session:
        return session.exec(
            select(ImportedLink)
            .where(ImportedLink.is_product_detail == True)
            .where(ImportedLink.is_scraped == False)
            .order_by(ImportedLink.id)
        ).all()


def get_all_search_links(engine) -> list[ImportedLink]:
    """Return all ImportedLink rows where is_product_detail=False and not yet scraped."""
    with Session(engine) as session:
        return session.exec(
            select(ImportedLink)
            .where(ImportedLink.is_product_detail == False)
            .where(ImportedLink.is_scraped == False)
            .order_by(ImportedLink.id)
        ).all()


def mark_imported_link_scraped(engine, link_id: int):
    """Set is_scraped=True on the ImportedLink record."""
    with Session(engine) as session:
        rec = session.exec(
            select(ImportedLink).where(ImportedLink.id == link_id)
        ).first()
        if rec:
            rec.is_scraped = True
            session.add(rec)
            session.commit()


def clear_links_scraped_data_for_link(engine, link_id: int):
    """Delete all links_scraped_data rows for a given link_id (before re-scraping)."""
    with Session(engine) as session:
        records = session.exec(
            select(LinkScrapedData).where(LinkScrapedData.link_id == link_id)
        ).all()
        for rec in records:
            session.delete(rec)
        session.commit()


def insert_link_scraped_rows(engine, link_id: int, link_url: str, rows: list[dict]) -> int:
    """Insert a list of scraped row dicts into links_scraped_data. Returns count inserted."""
    with Session(engine) as session:
        for row in rows:
            session.add(LinkScrapedData(
                link_id=link_id,
                link=link_url,
                manufacturer_part_name=row.get("manufacturer_part_name"),
                manufacturer_part_number=row.get("manufacturer_part_number"),
                product_name=row.get("product_name"),
                price=row.get("price"),
                unit=row.get("unit"),
                contractor_name=row.get("contractor_name"),
                contract_number=row.get("contract_number"),
                row_order=row.get("row_order", 0),
            ))
        session.commit()
    return len(rows)


def get_all_links_scraped_data(engine) -> list[LinkScrapedData]:
    """Return all rows from links_scraped_data ordered by link_id and row_order."""
    with Session(engine) as session:
        return session.exec(
            select(LinkScrapedData)
            .order_by(LinkScrapedData.link_id, LinkScrapedData.row_order)
        ).all()


def clear_links_scraped_data(engine):
    """Delete ALL rows from links_scraped_data in a single atomic SQL DELETE."""
    with Session(engine) as session:
        session.exec(_sql_delete(LinkScrapedData))
        session.commit()


# ── Jobs ───────────────────────────────────────────────────────

def create_job(engine, job_type: str, input_filename: str, input_row_count: int) -> Job:
    with Session(engine) as session:
        job = Job(type=job_type, input_filename=input_filename, input_row_count=input_row_count)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def update_job_input_s3(engine, job_id: int, s3_key: str) -> None:
    with Session(engine) as session:
        job = session.exec(select(Job).where(Job.id == job_id)).first()
        if job:
            job.input_s3_key = s3_key
            session.add(job)
            session.commit()


def update_job_status(engine, job_id: int, status: str) -> None:
    with Session(engine) as session:
        job = session.exec(select(Job).where(Job.id == job_id)).first()
        if job:
            job.status = status
            if status in ("completed", "failed"):
                job.completed_at = datetime.utcnow()
            session.add(job)
            session.commit()


def update_job_output(engine, job_id: int, output_s3_key: str, output_filename: str) -> None:
    with Session(engine) as session:
        job = session.exec(select(Job).where(Job.id == job_id)).first()
        if job:
            job.output_s3_key = output_s3_key
            job.output_filename = output_filename
            session.add(job)
            session.commit()


def get_all_jobs(engine) -> list[Job]:
    with Session(engine) as session:
        return session.exec(select(Job).order_by(Job.created_at.desc())).all()


def get_job(engine, job_id: int) -> Optional[Job]:
    with Session(engine) as session:
        return session.exec(select(Job).where(Job.id == job_id)).first()


def clear_imported_links_scraped_flags(engine):
    """Reset is_scraped=False on all ImportedLink rows (used when links are re-imported)."""
    with Session(engine) as session:
        records = session.exec(select(ImportedLink)).all()
        for rec in records:
            rec.is_scraped = False
            session.add(rec)
        session.commit()
