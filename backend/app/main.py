"""Point d'entrée FastAPI — CHR Signal Radar (PoC)."""
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import Session, select

from .database import get_session, init_db
from .models import (
    CHANNELS,
    ESTABLISHMENT_TYPES,
    SIGNAL_TYPES,
    STATUSES,
    Opportunity,
)
from .routes import dashboard, messages, opportunities, pipeline, settings

app = FastAPI(title="CHR Signal Radar API", version="0.1.0")

# CORS : autorise le frontend Next.js en local.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


app.include_router(dashboard.router)
app.include_router(opportunities.router)
app.include_router(messages.router)
app.include_router(pipeline.router)
app.include_router(settings.router)


@app.get("/")
def root():
    return {"name": "CHR Signal Radar API", "status": "ok", "docs": "/docs"}


# --- Métadonnées (options de filtres pour le frontend) ------------------------

meta_router = APIRouter(prefix="/api/meta", tags=["meta"])


@meta_router.get("")
def get_meta(session: Session = Depends(get_session)):
    cities = sorted(set(session.exec(select(Opportunity.city)).all()))
    return {
        "establishment_types": ESTABLISHMENT_TYPES,
        "signal_types": SIGNAL_TYPES,
        "channels": CHANNELS,
        "statuses": STATUSES,
        "cities": cities,
    }


app.include_router(meta_router)


# --- Endpoints de développement : seed & ingestion ----------------------------

dev_router = APIRouter(prefix="/api/dev", tags=["dev"])


@dev_router.post("/seed")
def run_seed():
    from .seed import seed

    count = seed()
    return {"seeded": count}


class IngestRequest(BaseModel):
    source: str = "bodacc"
    since_days: int = 90
    limit: int = 100
    departments: Optional[List[str]] = None
    reset: bool = False
    enrich: bool = True


@dev_router.post("/ingest")
def run_ingest(payload: IngestRequest):
    from .ingestion.pipeline import run_ingestion, stats_to_dict

    try:
        stats = run_ingestion(
            source=payload.source,
            since_days=payload.since_days,
            limit=payload.limit,
            departments=payload.departments,
            reset=payload.reset,
            enrich=payload.enrich,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # erreur réseau / source indisponible
        raise HTTPException(status_code=502, detail=f"Ingestion échouée : {exc}")

    return stats_to_dict(stats)


@dev_router.post("/ingest/incremental")
def run_ingest_incremental(source: str = "bodacc"):
    from .ingestion.pipeline import run_incremental, stats_to_dict

    try:
        return stats_to_dict(run_incremental(source=source))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ingestion échouée : {exc}")


@dev_router.post("/ingest/backfill")
def run_ingest_backfill(source: str = "bodacc", since_days: int = 120):
    from .ingestion.pipeline import run_backfill, stats_to_dict

    try:
        return stats_to_dict(run_backfill(source=source, since_days=since_days))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ingestion échouée : {exc}")


@dev_router.post("/reenrich")
def run_reenrich_endpoint(source: str = "bodacc"):
    from .ingestion.pipeline import run_reenrich, stats_to_dict

    try:
        return stats_to_dict(run_reenrich(source=source))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ré-enrichissement échoué : {exc}")


@dev_router.post("/contact-enrich")
def run_contact_enrich_endpoint(source: str = "bodacc", limit: int = 500):
    from .ingestion.pipeline import run_contact_enrich, stats_to_dict

    try:
        return stats_to_dict(run_contact_enrich(source=source, limit=limit))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Enrichissement contact échoué : {exc}")


app.include_router(dev_router)
