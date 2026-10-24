from pathlib import Path
import os
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from dotenv import load_dotenv

# __file__ is 'backend/db/postgres.py'
project_root = Path(__file__).resolve().parents[2] # climbs up 2 levels to project root

load_dotenv(project_root / ".env")


def postgres_url():
    return URL.create(
        "postgresql+psycopg",
        username=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB"),
    )


def postgres_engine():
    return create_engine(postgres_url())
