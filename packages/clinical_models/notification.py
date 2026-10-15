"""Clinical Notification Models and Delivery State Machine for SevaHealth AI.
Supports multi-channel clinical alerts (SMS, WhatsApp, Push), gateway failure simulation,
exponential/fixed retry policies, dead-letter transitions, and duplicate event deduplication.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, computed_field
import uuid

from packages.types.enums import NotificationDeliveryStatus, NotificationChannel


class NotificationDeliveryAttempt(BaseModel):
    """Detailed record of each transmission attempt for clinical auditability."""
    attempt_number: int
    attempted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "FAILED"  # "SUCCESS" | "FAILED"
    success: bool = False
    response_code: int = 200
    error_message: Optional[str] = None
    channel: str = "SMS"
    latency_ms: float = 15.0


class ClinicalNotification(BaseModel):
    """Auditable clinical notification tracking delivery attempts, failures, and retries."""
    id: str = Field(default_factory=lambda: f"notif-{uuid.uuid4().hex[:8]}")
    tenant_id: str
    recipient_id: str
    recipient_role: str = "CLINICIAN"
    citizen_id: str
    associated_resource_type: str = "ClinicalTriageCase"  # "ClinicalTriageCase" | "Referral"
    associated_resource_id: str
    channel: str = "SMS"
    title: str
    body: str
    urgency: str = "EMERGENT"
    status: NotificationDeliveryStatus = NotificationDeliveryStatus.PENDING
    delivery_attempts: int = 0
    max_retries: int = 3
    attempts_history: List[NotificationDeliveryAttempt] = Field(default_factory=list)
    failure_reason: Optional[str] = None
    last_attempt_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def record_attempt(
        self,
        success: bool,
        error_message: Optional[str] = None,
        response_code: int = 200,
        latency_ms: float = 15.0,
    ) -> NotificationDeliveryStatus:
        """Records delivery attempt and updates state machine according to policy."""
        now = datetime.now(timezone.utc)
        self.delivery_attempts += 1
        self.last_attempt_at = now

        attempt_record = NotificationDeliveryAttempt(
            attempt_number=self.delivery_attempts,
            attempted_at=now,
            status="SUCCESS" if success else "FAILED",
            success=success,
            response_code=response_code,
            error_message=error_message,
            channel=self.channel,
            latency_ms=latency_ms,
        )
        self.attempts_history.append(attempt_record)

        if success:
            self.status = NotificationDeliveryStatus.DELIVERED
            self.delivered_at = now
            self.failure_reason = None
        else:
            self.failure_reason = error_message or "Upstream gateway delivery failure"
            if self.delivery_attempts >= self.max_retries:
                self.status = NotificationDeliveryStatus.DEAD_LETTER
            else:
                self.status = NotificationDeliveryStatus.FAILED

        return self.status

    @computed_field
    @property
    def can_retry(self) -> bool:
        return (
            self.status not in (NotificationDeliveryStatus.DELIVERED, NotificationDeliveryStatus.DEAD_LETTER)
            and self.delivery_attempts < self.max_retries
        )


# API Payloads
class TriggerNotificationPayload(BaseModel):
    citizen_id: str
    associated_resource_id: str
    associated_resource_type: str = "ClinicalTriageCase"
    channel: str = "SMS"
    urgency: str = "EMERGENT"
    title: Optional[str] = None
    body: Optional[str] = None
    simulate_provider_failure: bool = False
    failure_status_code: int = 503
    failure_error_message: str = "Telecom gateway 503 Service Unavailable: simulated upstream provider failure"


class RetryNotificationPayload(BaseModel):
    simulate_provider_failure: bool = False
    failure_status_code: int = 503
    failure_error_message: str = "Telecom gateway 503 Service Unavailable: simulated upstream provider failure"
    notes: Optional[str] = None


class EscalationEventPayload(BaseModel):
    """Payload for submitting an acute clinical escalation event (TC-CLN-005)."""
    citizen_id: str
    event_type: str = "ACUTE_BIOMETRIC_CRISIS"
    idempotency_key: Optional[str] = None
    urgency: str = "EMERGENT"
    reason: str
    key_observations: Dict[str, Any] = Field(default_factory=dict)
    deduplication_window_seconds: int = 600  # 10 minutes deduplication window


class EscalationEventResponse(BaseModel):
    case_id: str
    citizen_id: str
    urgency: str
    status: str
    is_duplicate: bool = False
    duplicate_count: int = 0
    duplicate_action: str = "CREATED"  # "CREATED" | "SUPPRESSED_AND_LINKED"
    message: str
    created_at: datetime
