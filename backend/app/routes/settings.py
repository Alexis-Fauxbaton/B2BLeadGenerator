"""Endpoints des réglages du fournisseur."""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..database import get_session
from ..models import Settings
from ..schemas import SettingsRead, SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get_or_create(session: Session) -> Settings:
    settings = session.exec(select(Settings)).first()
    if not settings:
        settings = Settings()
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


@router.get("", response_model=SettingsRead)
def get_settings(session: Session = Depends(get_session)):
    return _get_or_create(session)


@router.patch("", response_model=SettingsRead)
def update_settings(payload: SettingsUpdate, session: Session = Depends(get_session)):
    settings = _get_or_create(session)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(settings, key, value)
    settings.updated_at = datetime.utcnow()
    session.add(settings)
    session.commit()
    session.refresh(settings)
    return settings
