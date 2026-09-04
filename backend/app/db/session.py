"""
Engine/session management. Reads DATABASE_URL from environment (see
.env.example). Uses a simple session-per-request pattern via FastAPI's
dependency injection (see queries.py usage in main.py).
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://airfare_user:airfare_pass@localhost:5432/airfare_index",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """FastAPI dependency: yields a session, always closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
