import threading
import logging
from typing import Dict, Any
from datetime import datetime
from app.config import settings
from app.database import SessionLocal
from app.models import PipelineRun, TestCase, PipelineStatus, ApprovalStatus, TestCaseStatus
from app.services.slack import SlackService

logger = logging.getLogger(__name__)


class SLATimerService:
    def __init__(self):
        self.warn_hours = settings.APPROVAL_SLA_WARN_HOURS
        self.reject_hours = settings.APPROVAL_SLA_REJECT_HOURS
        self.enabled = settings.DEMO_MODE is False
        self._timers: Dict[str, Dict[str, threading.Timer]] = {}

    def schedule(self, pipeline_run_id: str, task_id: str, task_url: str) -> None:
        """Schedule 4h warning and 24h auto-reject for a pending approval."""
        if not self.enabled:
            return

        self._cancel_existing(pipeline_run_id)

        # Schedule 4h warning
        warn_timer = threading.Timer(
            self.warn_hours * 3600,
            self._warn_callback,
            args=[pipeline_run_id, task_url]
        )
        warn_timer.daemon = True
        warn_timer.start()

        # Schedule 24h auto-reject
        reject_timer = threading.Timer(
            self.reject_hours * 3600,
            self._reject_callback,
            args=[pipeline_run_id, task_id]
        )
        reject_timer.daemon = True
        reject_timer.start()

        self._timers[pipeline_run_id] = {
            "warn": warn_timer,
            "reject": reject_timer
        }

    def _cancel_existing(self, pipeline_run_id: str) -> None:
        """Cancel any existing timers for this pipeline run."""
        if pipeline_run_id in self._timers:
            timers = self._timers.pop(pipeline_run_id)
            for t in timers.values():
                t.cancel()

    def _warn_callback(self, pipeline_run_id: str, task_url: str) -> None:
        """Post Slack warning for overdue approval."""
        try:
            slack = SlackService()
            slack.send_notification(
                "Approval Overdue",
                f"Action Center task for run `{pipeline_run_id}` has been pending for {self.warn_hours} hours.\n<{task_url}|Review approval>"
            )
        except Exception as e:
            logger.error(f"SLA warning failed: {e}")

    def _reject_callback(self, pipeline_run_id: str, task_id: str) -> None:
        """Auto-reject all pending tests and cancel Action Center task."""
        try:
            db = SessionLocal()
            try:
                # Cancel Action Center task
                from app.services.action_center import ActionCenterService
                ac = ActionCenterService()
                ac.cancel_task(task_id)

                # Set all pending test cases to rejected
                tests = db.query(TestCase).filter(
                    TestCase.pipeline_run_id == pipeline_run_id,
                    TestCase.approval_status == ApprovalStatus.pending
                ).all()
                for tc in tests:
                    tc.approval_status = ApprovalStatus.rejected
                    tc.status = TestCaseStatus.rejected

                # Mark pipeline as failed
                pipeline = db.query(PipelineRun).filter(PipelineRun.id == pipeline_run_id).first()
                if pipeline:
                    pipeline.status = PipelineStatus.failed
                    # Add note about timeout (store in linked_issue_id or create a new field)
                    if pipeline.linked_issue_id is None:
                        pipeline.linked_issue_id = "approval_timeout"

                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.error(f"SLA auto-reject failed: {e}")
        finally:
            self._cancel_existing(pipeline_run_id)

    def cancel(self, pipeline_run_id: str) -> None:
        """Cancel timers when pipeline leaves awaiting_approval state."""
        self._cancel_existing(pipeline_run_id)


# Global instance
sla_timer = SLATimerService()
