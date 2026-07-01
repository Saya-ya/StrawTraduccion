"""SQLAlchemy database setup and ORM models."""
from sqlalchemy import (create_engine, Column, Integer, String, Text,
                        Boolean, DateTime, ForeignKey, Index)
from sqlalchemy.orm import DeclarativeBase, sessionmaker, relationship
from datetime import datetime, timezone

from .config import DB_URL

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class Script(Base):
    __tablename__ = "scripts"

    id = Column(Integer, primary_key=True)          # file_id
    source = Column(String(10), default="SCRIPT")    # SCRIPT or ELF
    script_type = Column(String(30), default="")
    offset_in_bin = Column(Integer, default=0)
    size_in_bin = Column(Integer, default=0)
    slot_capacity = Column(Integer, default=0)
    is_supported = Column(Boolean, default=False)
    total_texts = Column(Integer, default=0)
    translated_texts = Column(Integer, default=0)

    texts = relationship("TextEntry", back_populates="script",
                         order_by="TextEntry.byte_offset")


class TextEntry(Base):
    __tablename__ = "text_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    script_id = Column(Integer, ForeignKey("scripts.id"), nullable=False)
    source = Column(String(10), default="SCRIPT")
    byte_offset = Column(Integer, nullable=False)
    original_text = Column(Text, nullable=False)
    translated_text = Column(Text, default="")
    original_bytes = Column(Integer, default=0)
    segment_start = Column(Integer, default=0)
    segment_capacity = Column(Integer, default=0)
    is_translated = Column(Boolean, default=False)
    needs_shift = Column(Boolean, default=False)
    fit_status = Column(String(20), default="unchecked")
    locked_by = Column(String(50), default="")
    locked_at = Column(DateTime, default=None)
    updated_at = Column(DateTime, default=None)

    script = relationship("Script", back_populates="texts")

    __table_args__ = (
        Index("ix_script_offset", "script_id", "byte_offset"),
        Index("ix_translated", "is_translated"),
    )


class BuildHistory(Base):
    __tablename__ = "build_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime, default=None)
    status = Column(String(20), default="running")
    build_type = Column(String(20), default="full")
    script_id = Column(Integer, default=None)
    iso_path = Column(String(500), default="")
    error_log = Column(Text, default="")
    step = Column(String(200), default="")
    progress_pct = Column(Integer, default=0)


def init_db():
    """Create all tables and FTS5 index."""
    Base.metadata.create_all(engine)

    # FTS5 virtual table for full-text search
    with engine.connect() as conn:
        conn.exec_driver_sql("""
            CREATE VIRTUAL TABLE IF NOT EXISTS text_entries_fts USING fts5(
                original_text, translated_text,
                content='text_entries',
                content_rowid='id'
            )
        """)
        # Triggers to keep FTS5 in sync
        conn.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS text_entries_ai AFTER INSERT ON text_entries BEGIN
                INSERT INTO text_entries_fts(rowid, original_text, translated_text)
                VALUES (new.id, new.original_text, new.translated_text);
            END
        """)
        conn.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS text_entries_ad AFTER DELETE ON text_entries BEGIN
                INSERT INTO text_entries_fts(text_entries_fts, rowid, original_text, translated_text)
                VALUES ('delete', old.id, old.original_text, old.translated_text);
            END
        """)
        conn.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS text_entries_au AFTER UPDATE ON text_entries BEGIN
                INSERT INTO text_entries_fts(text_entries_fts, rowid, original_text, translated_text)
                VALUES ('delete', old.id, old.original_text, old.translated_text);
                INSERT INTO text_entries_fts(rowid, original_text, translated_text)
                VALUES (new.id, new.original_text, new.translated_text);
            END
        """)
        conn.commit()

    # Populate FTS from existing data (only if FTS is empty)
    with engine.connect() as conn:
        count = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM text_entries_fts"
        ).scalar()
        if count == 0:
            conn.exec_driver_sql("""
                INSERT INTO text_entries_fts(rowid, original_text, translated_text)
                SELECT id, original_text, translated_text FROM text_entries
            """)
            conn.commit()


def get_session():
    """Get a new database session."""
    return SessionLocal()
