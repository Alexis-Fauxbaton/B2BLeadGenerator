"""Configuration de la base de données SQLite via SQLModel."""
import os

from dotenv import load_dotenv
from sqlmodel import Session, SQLModel, create_engine

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./chr_signal_radar.db")

# check_same_thread=False : nécessaire car FastAPI peut utiliser plusieurs threads.
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


def init_db() -> None:
    """Crée les tables si elles n'existent pas encore, puis applique les
    migrations légères (ajout de colonnes manquantes sur une base existante)."""
    # Import nécessaire pour que SQLModel connaisse les modèles avant create_all.
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _run_lightweight_migrations()


def _run_lightweight_migrations() -> None:
    """Ajoute les colonnes ajoutées après coup (SQLite n'a pas de IF NOT EXISTS
    sur ADD COLUMN ; on inspecte donc PRAGMA table_info)."""
    from sqlalchemy import inspect, text

    if not DATABASE_URL.startswith("sqlite"):
        return

    inspector = inspect(engine)
    if "opportunities" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("opportunities")}
    additions = {
        "source": "ALTER TABLE opportunities ADD COLUMN source VARCHAR DEFAULT 'demo'",
        "source_ref": "ALTER TABLE opportunities ADD COLUMN source_ref VARCHAR",
        "siren": "ALTER TABLE opportunities ADD COLUMN siren VARCHAR",
        "naf": "ALTER TABLE opportunities ADD COLUMN naf VARCHAR",
        "siret": "ALTER TABLE opportunities ADD COLUMN siret VARCHAR",
        "siren_match_method": "ALTER TABLE opportunities ADD COLUMN siren_match_method VARCHAR",
        "siren_match_confidence": "ALTER TABLE opportunities ADD COLUMN siren_match_confidence VARCHAR",
        "phone": "ALTER TABLE opportunities ADD COLUMN phone VARCHAR",
        "email": "ALTER TABLE opportunities ADD COLUMN email VARCHAR",
        "website": "ALTER TABLE opportunities ADD COLUMN website VARCHAR",
        "instagram": "ALTER TABLE opportunities ADD COLUMN instagram VARCHAR",
        "facebook": "ALTER TABLE opportunities ADD COLUMN facebook VARCHAR",
        "latitude": "ALTER TABLE opportunities ADD COLUMN latitude REAL",
        "longitude": "ALTER TABLE opportunities ADD COLUMN longitude REAL",
        "dirigeants": "ALTER TABLE opportunities ADD COLUMN dirigeants JSON",
        "activity_start_date": "ALTER TABLE opportunities ADD COLUMN activity_start_date DATE",
        "venue_origin_date": "ALTER TABLE opportunities ADD COLUMN venue_origin_date DATE",
        "review_count": "ALTER TABLE opportunities ADD COLUMN review_count INTEGER",
        "contact_confidence": "ALTER TABLE opportunities ADD COLUMN contact_confidence VARCHAR",
        "decision_maker_email": "ALTER TABLE opportunities ADD COLUMN decision_maker_email VARCHAR",
        "decision_maker_confidence": "ALTER TABLE opportunities ADD COLUMN decision_maker_confidence VARCHAR",
        "contact_enriched_at": "ALTER TABLE opportunities ADD COLUMN contact_enriched_at DATETIME",
        "last_checked_at": "ALTER TABLE opportunities ADD COLUMN last_checked_at DATETIME",
        "closed_at": "ALTER TABLE opportunities ADD COLUMN closed_at DATETIME",
        "extra_addresses": "ALTER TABLE opportunities ADD COLUMN extra_addresses JSON",
        "extra_emails": "ALTER TABLE opportunities ADD COLUMN extra_emails JSON",
        "lifecycle_label": "ALTER TABLE opportunities ADD COLUMN lifecycle_label VARCHAR",
        "population": "ALTER TABLE opportunities ADD COLUMN population VARCHAR DEFAULT 'chr'",
    }
    with engine.begin() as conn:
        for column, ddl in additions.items():
            if column not in existing:
                conn.execute(text(ddl))


def get_session():
    """Dépendance FastAPI : fournit une session DB par requête."""
    with Session(engine) as session:
        yield session
