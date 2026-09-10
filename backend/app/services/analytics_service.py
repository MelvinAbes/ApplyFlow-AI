from sqlmodel import Session, select

from app.models import Application, Job, JobAnalysisRecord
from app.models.enums import ApplicationStatus
from app.schemas.application import DashboardStats, FunnelAnalytics


class AnalyticsService:
    def __init__(self, session: Session):
        self.session = session

    def dashboard(self) -> DashboardStats:
        jobs = self.session.exec(select(Job)).all()
        applications = self.session.exec(select(Application)).all()
        analyses = self.session.exec(
            select(JobAnalysisRecord).order_by(JobAnalysisRecord.created_at.desc())
        ).all()
        latest_by_job: dict[str, JobAnalysisRecord] = {}
        for analysis in analyses:
            latest_by_job.setdefault(analysis.job_id, analysis)
        top = sorted(
            latest_by_job.values(),
            key=lambda item: item.result.get("overall_score", 0),
            reverse=True,
        )[:5]
        highest = []
        for analysis in top:
            job = self.session.get(Job, analysis.job_id)
            if job:
                highest.append(
                    {
                        "job_id": job.id,
                        "company": job.company,
                        "role": job.title,
                        "score": analysis.result.get("overall_score", 0),
                    }
                )
        queue_statuses = {
            ApplicationStatus.NEEDS_USER_INPUT,
            ApplicationStatus.READY_TO_ADVANCE,
            ApplicationStatus.READY_FOR_REVIEW,
            ApplicationStatus.SUBMISSION_UNCONFIRMED,
            ApplicationStatus.WAITING_FOR_USER,
        }
        queue = [
            self._application_summary(app) for app in applications if app.status in queue_statuses
        ]
        submitted = [app for app in applications if app.submitted_at]
        submitted.sort(key=lambda app: app.submitted_at or app.created_at, reverse=True)
        return DashboardStats(
            jobs_discovered=len(jobs),
            strong_matches=sum(
                1
                for analysis in latest_by_job.values()
                if analysis.result.get("overall_score", 0) >= 80
            ),
            ready_to_apply=sum(
                1 for app in applications if app.status == ApplicationStatus.READY_FOR_REVIEW
            ),
            applications_submitted=sum(
                1 for app in applications if app.status in self._submitted_outcomes()
            ),
            interviews=sum(1 for app in applications if app.status == ApplicationStatus.INTERVIEW),
            offers=sum(1 for app in applications if app.status == ApplicationStatus.OFFER),
            highest_matches=highest,
            application_queue=queue[:5],
            recent_applications=[self._application_summary(app) for app in submitted[:5]],
        )

    def funnel(self) -> FunnelAnalytics:
        applications = self.session.exec(select(Application)).all()
        submitted = [app for app in applications if app.status in self._submitted_outcomes()]
        interviews = sum(
            1
            for app in applications
            if app.status in {ApplicationStatus.INTERVIEW, ApplicationStatus.OFFER}
        )
        offers = sum(1 for app in applications if app.status == ApplicationStatus.OFFER)
        rejections = sum(1 for app in applications if app.status == ApplicationStatus.REJECTED)
        withdrawn = sum(1 for app in applications if app.status == ApplicationStatus.WITHDRAWN)
        pending = max(0, len(submitted) - rejections - interviews - withdrawn)
        return FunnelAnalytics(
            applications=len(submitted),
            rejections=rejections,
            interviews=interviews,
            offers=offers,
            pending=pending,
            application_to_interview_rate=round(interviews / len(submitted) * 100, 1)
            if submitted
            else 0,
            interview_to_offer_rate=round(offers / interviews * 100, 1) if interviews else 0,
        )

    def _application_summary(self, application: Application) -> dict:
        job = self.session.get(Job, application.job_id)
        return {
            "application_id": application.id,
            "company": job.company if job else "Unknown",
            "role": job.title if job else "Unknown",
            "status": application.status.value,
            "date": application.submitted_at or application.created_at,
        }

    @staticmethod
    def _submitted_outcomes() -> set[ApplicationStatus]:
        return {
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.REJECTED,
            ApplicationStatus.INTERVIEW,
            ApplicationStatus.OFFER,
            ApplicationStatus.WITHDRAWN,
        }
