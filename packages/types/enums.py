from enum import Enum


class UserRole(str, Enum):
    """Platform Role-Based Access Control Taxonomy (adapted from bezs-iam)."""
    CITIZEN = "CITIZEN"
    HEALTH_WORKER = "HEALTH_WORKER"
    CLINICIAN = "CLINICIAN"
    PUBLIC_HEALTH_ADMIN = "PUBLIC_HEALTH_ADMIN"
    SYSTEM_ADMIN = "SYSTEM_ADMIN"


class Gender(str, Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"


class RiskTier(str, Enum):
    """Composite and domain risk tier classification."""
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class TrajectoryTrend(str, Enum):
    """Longitudinal progression trajectory."""
    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    DETERIORATING = "DETERIORATING"


class TriageUrgency(str, Enum):
    """Urgency level for clinician triage routing."""
    ROUTINE = "ROUTINE"       # Within 30-90 days
    PRIORITY = "PRIORITY"     # Within 14 days
    URGENT = "URGENT"         # Within 48 hours
    EMERGENT = "EMERGENT"     # Immediate same-day evaluation


class ClinicianReviewStatus(str, Enum):
    """Human-in-the-loop clinical review state machine."""
    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REFERRED = "REFERRED"
    REJECTED = "REJECTED"


class ReferralStatus(str, Enum):
    """State machine taxonomy for closed-loop clinical and community referrals."""
    PENDING = "PENDING"          # Created, awaiting destination assignment / review
    ASSIGNED = "ASSIGNED"        # Assigned to destination facility / clinician
    IN_PROGRESS = "IN_PROGRESS"  # Arrived at destination / consultation in progress
    COMPLETED = "COMPLETED"      # Consultation conducted and closed with outcome
    CANCELLED = "CANCELLED"      # Cancelled / revoked prior to completion


class InterventionPillar(str, Enum):
    """Lifestyle medicine pillars for preventive care."""
    NUTRITION = "NUTRITION"
    PHYSICAL_ACTIVITY = "PHYSICAL_ACTIVITY"
    SLEEP_HYGIENE = "SLEEP_HYGIENE"
    STRESS_AND_LIFESTYLE = "STRESS_AND_LIFESTYLE"


class AuditAction(str, Enum):
    """Audit action types (adapted from bezs-observability)."""
    USER_REGISTERED = "USER_REGISTERED"
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGOUT = "LOGOUT"
    PASSWORD_RESET = "PASSWORD_RESET"
    CONSENT_GRANTED = "CONSENT_GRANTED"
    CONSENT_REVOKED = "CONSENT_REVOKED"
    ACCESS_DENIED = "ACCESS_DENIED"
    SCREENING_SUBMITTED = "SCREENING_SUBMITTED"
    RISK_ASSESSED = "RISK_ASSESSED"
    CARE_PLAN_GENERATED = "CARE_PLAN_GENERATED"
    TASK_COMPLETED = "TASK_COMPLETED"
    WEARABLE_SYNCED = "WEARABLE_SYNCED"
    CLINICIAN_REVIEWED = "CLINICIAN_REVIEWED"
    CLINICIAN_ACKNOWLEDGED = "CLINICIAN_ACKNOWLEDGED"
    CARE_PLAN_REVIEWED = "CARE_PLAN_REVIEWED"
    CHECKIN_RECORDED = "CHECKIN_RECORDED"
    OBSERVATION_RECORDED = "OBSERVATION_RECORDED"
    DATA_EXPORTED = "DATA_EXPORTED"
    REFERRAL_ISSUED = "REFERRAL_ISSUED"
    REFERRAL_UPDATED = "REFERRAL_UPDATED"
    REFERRAL_CLOSED = "REFERRAL_CLOSED"
    FOLLOWUP_SCHEDULED = "FOLLOWUP_SCHEDULED"
    SECURITY_ALERT = "SECURITY_ALERT"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    NOTIFICATION_TRIGGERED = "NOTIFICATION_TRIGGERED"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    NOTIFICATION_RETRIED = "NOTIFICATION_RETRIED"
    NOTIFICATION_DELIVERED = "NOTIFICATION_DELIVERED"
    DUPLICATE_ESCALATION_SUPPRESSED = "DUPLICATE_ESCALATION_SUPPRESSED"


class NotificationDeliveryStatus(str, Enum):
    """Delivery status state machine for multi-channel clinical alerts."""
    PENDING = "PENDING"
    SENDING = "SENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    DEAD_LETTER = "DEAD_LETTER"


class NotificationChannel(str, Enum):
    """Supported communication channels for healthcare notifications."""
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"
    PUSH = "PUSH"
    EMAIL = "EMAIL"

