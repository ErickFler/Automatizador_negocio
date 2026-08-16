from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def utc_now() -> datetime:
    return datetime.now().astimezone()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Branch(TimestampMixin, Base):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    branch_products: Mapped[list["BranchProduct"]] = relationship(
        back_populates="branch"
    )
    import_batches: Mapped[list["ImportBatch"]] = relationship(back_populates="branch")
    sale_records: Mapped[list["SaleRecord"]] = relationship(back_populates="branch")


class Category(TimestampMixin, Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    master_products: Mapped[list["MasterProduct"]] = relationship(
        back_populates="category"
    )


class MasterProduct(TimestampMixin, Base):
    __tablename__ = "master_products"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id"), index=True, nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    category: Mapped[Category] = relationship(back_populates="master_products")
    branch_products: Mapped[list["BranchProduct"]] = relationship(
        back_populates="master_product"
    )
    sale_records: Mapped[list["SaleRecord"]] = relationship(back_populates="product")


class BranchProduct(TimestampMixin, Base):
    __tablename__ = "branch_products"
    __table_args__ = (
        UniqueConstraint(
            "branch_id",
            "external_product_id",
            name="uq_branch_products_branch_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id"), index=True, nullable=False
    )
    external_product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    original_product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_product_name: Mapped[str] = mapped_column(
        String(255), index=True, nullable=False
    )
    original_category_name: Mapped[str | None] = mapped_column(String(255))
    master_product_id: Mapped[int] = mapped_column(
        ForeignKey("master_products.id"), index=True, nullable=False
    )

    branch: Mapped[Branch] = relationship(back_populates="branch_products")
    master_product: Mapped[MasterProduct] = relationship(
        back_populates="branch_products"
    )


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint(
            "sales_date",
            "branch_id",
            "sha256",
            name="uq_import_batches_date_branch_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sales_date: Mapped[date] = mapped_column(Date, index=True)
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id"), index=True, nullable=False
    )
    source_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(255), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    warning_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    error_message: Mapped[str | None] = mapped_column(Text)

    branch: Mapped[Branch] = relationship(back_populates="import_batches")
    raw_sale_records: Mapped[list["RawSaleRecord"]] = relationship(
        back_populates="import_batch", cascade="all, delete-orphan"
    )
    processing_issues: Mapped[list["ProcessingIssue"]] = relationship(
        back_populates="import_batch", cascade="all, delete-orphan"
    )
    sale_records: Mapped[list["SaleRecord"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class RawSaleRecord(Base):
    __tablename__ = "raw_sale_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id"), index=True, nullable=False
    )
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    original_category_name: Mapped[str | None] = mapped_column(Text)
    external_product_id: Mapped[str | None] = mapped_column(Text)
    original_product_name: Mapped[str | None] = mapped_column(Text)
    quantity_raw: Mapped[str | None] = mapped_column(Text)
    sale_price_raw: Mapped[str | None] = mapped_column(Text)
    cost_raw: Mapped[str | None] = mapped_column(Text)
    amount_raw: Mapped[str | None] = mapped_column(Text)
    total_amount_raw: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    import_batch: Mapped[ImportBatch] = relationship(
        back_populates="raw_sale_records"
    )
    processing_issues: Mapped[list["ProcessingIssue"]] = relationship(
        back_populates="raw_sale_record", cascade="all, delete-orphan"
    )


class SaleRecord(Base):
    __tablename__ = "sale_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("master_products.id"), index=True
    )
    sales_date: Mapped[date] = mapped_column(Date, index=True)
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id"), index=True, nullable=False
    )
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    real_units: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    combo_units: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    source_row_number: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    batch: Mapped[ImportBatch] = relationship(back_populates="sale_records")
    product: Mapped[MasterProduct] = relationship(back_populates="sale_records")
    branch: Mapped[Branch] = relationship(back_populates="sale_records")


class ProcessingIssue(Base):
    __tablename__ = "processing_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id"), index=True, nullable=False
    )
    raw_sale_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_sale_records.id"), index=True
    )
    source_row_number: Mapped[int | None] = mapped_column(Integer)
    issue_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    import_batch: Mapped[ImportBatch] = relationship(
        back_populates="processing_issues"
    )
    raw_sale_record: Mapped[RawSaleRecord | None] = relationship(
        back_populates="processing_issues"
    )
