from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime


class Job(SQLModel, table=True):
    __tablename__ = 'jobs'
    id: int = Field(default=None, primary_key=True)
    type: str = Field()                    # "parts" | "links"
    status: str = Field(default="pending") # "pending" | "running" | "completed" | "failed"
    input_filename: str = Field()
    input_row_count: int = Field(default=0)
    input_s3_key: Optional[str] = Field(default=None, nullable=True)
    output_s3_key: Optional[str] = Field(default=None, nullable=True)
    output_filename: Optional[str] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = Field(default=None, nullable=True)

class GSALink(SQLModel, table=True):
    __tablename__ = 'gsa_links'
    part_number: str = Field(primary_key=True)
    gsa_link: str = Field()
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_scraped: bool = Field(default=False)


class ImportedLink(SQLModel, table=True):
    __tablename__ = 'imported_links'
    id: int = Field(default=None, primary_key=True)
    link: str = Field()
    part_number: str = Field(default=None, nullable=True)
    is_product_detail: bool = Field(default=False)
    link_type: str = Field(default="internal")  # "internal" or "external"
    is_scraped: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ImportedPart(SQLModel, table=True):
    __tablename__ = 'imported_parts'
    id: int = Field(default=None, primary_key=True)
    part_number: str = Field(index=True)
    manufacturer: str = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GSAScrapedData(SQLModel, table=True):
    __tablename__ = 'gsa_scraped_data'
    id: int = Field(default=None, primary_key=True)
    part_number: str = Field(index=True)
    gsa_low_price_1: float = Field(default=None)
    unit_1: str = Field(default=None)
    contractor_1: str = Field(default=None)
    gsa_low_price_2: float = Field(default=None)
    unit_2: str = Field(default=None)
    contractor_2: str = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LinkScrapedData(SQLModel, table=True):
    """Stores rows scraped from the Compare Available Sources modal on product detail pages."""
    __tablename__ = 'links_scraped_data'
    id: int = Field(default=None, primary_key=True)
    link_id: int = Field(index=True)                         # FK → imported_links.id
    link: str = Field()                                       # the original product detail URL
    manufacturer_part_name: Optional[str] = Field(default=None, nullable=True)
    manufacturer_part_number: Optional[str] = Field(default=None, nullable=True)
    product_name: Optional[str] = Field(default=None, nullable=True)
    price: Optional[float] = Field(default=None, nullable=True)
    unit: Optional[str] = Field(default=None, nullable=True)
    contractor_name: Optional[str] = Field(default=None, nullable=True)
    contract_number: Optional[str] = Field(default=None, nullable=True)
    row_order: int = Field(default=0)                        # sort position (0 = first shown)
    created_at: datetime = Field(default_factory=datetime.utcnow)
