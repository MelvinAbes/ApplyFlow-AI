from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.database import get_session
from app.schemas.application import DashboardStats, FunnelAnalytics
from app.services.analytics_service import AnalyticsService

router = APIRouter(tags=["analytics"])


@router.get("/dashboard", response_model=DashboardStats)
def dashboard(session: Session = Depends(get_session)):
    return AnalyticsService(session).dashboard()


@router.get("/analytics/funnel", response_model=FunnelAnalytics)
def funnel(session: Session = Depends(get_session)):
    return AnalyticsService(session).funnel()
