"""REST API Endpoints for Mobile-First Health Worker Application."""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel

from packages.types.enums import UserRole
from packages.auth.jwt import get_current_user_token, TokenPayload, require_roles
from services.health_worker.models import (
    CommunityProgram,
    BatchSyncRequest,
    BatchSyncResponse,
    ReferralCreatePayload,
    ReferralRecord,
    FollowUpCreatePayload,
    FollowUpRecord,
    HealthWorkerDashboard,
)
from services.health_worker.service import health_worker_service

router = APIRouter(prefix="/health-worker", tags=["Health Worker Mobile API"])


@router.get("/programs", response_model=List[CommunityProgram])
async def list_programs(
    current_user: TokenPayload = Depends(require_roles([UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN, UserRole.CLINICIAN, UserRole.PUBLIC_HEALTH_ADMIN]))
):
    """Lists registered community health and NCD screening programs."""
    return health_worker_service.list_programs()


@router.post("/programs", response_model=CommunityProgram, status_code=status.HTTP_201_CREATED)
async def create_program(
    program: CommunityProgram,
    current_user: TokenPayload = Depends(require_roles([UserRole.SYSTEM_ADMIN, UserRole.PUBLIC_HEALTH_ADMIN]))
):
    """Registers a new community health and NCD screening program."""
    return health_worker_service.register_program(program)


@router.get("/dashboard", response_model=HealthWorkerDashboard)
async def get_worker_dashboard(
    current_user: TokenPayload = Depends(require_roles([UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN]))
):
    """Retrieves operational metrics, screening counts, and pending followups for worker."""
    return health_worker_service.get_dashboard(current_user)


@router.post("/sync", response_model=BatchSyncResponse)
async def batch_sync_drafts(
    request: BatchSyncRequest,
    current_user: TokenPayload = Depends(require_roles([UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN]))
):
    """Processes batch sync of offline drafts created in low-connectivity rural environments.
    Supports idempotency, automatic conflict detection and merging, and structured observation generation.
    """
    return health_worker_service.process_batch_sync(current_user, request)


@router.post("/register")
async def register_citizen_endpoint(
    payload: Dict[str, Any],
    current_user: TokenPayload = Depends(require_roles([UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN, UserRole.CLINICIAN]))
):
    """Direct citizen registration from the mobile field app."""
    try:
        cit, was_conflict = health_worker_service.register_citizen(current_user, payload)
        return {
            "status": "CONFLICT_RESOLVED" if was_conflict else "SUCCESS",
            "citizen": cit.to_dict(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/consent")
async def record_consent_endpoint(
    payload: Dict[str, Any],
    current_user: TokenPayload = Depends(require_roles([UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN, UserRole.CLINICIAN]))
):
    """Records ABDM-compliant informed consent from the citizen."""
    try:
        consent_dir = health_worker_service.record_consent(current_user, payload)
        return {
            "status": consent_dir.status,
            "consent_id": consent_dir.id,
            "citizen_id": consent_dir.citizen_id,
            "purpose": consent_dir.purpose,
            "scope": consent_dir.scope,
            "version": consent_dir.version,
            "notice_id": consent_dir.notice_id,
            "timestamp": consent_dir.granted_at.isoformat() if consent_dir.granted_at else datetime.now(timezone.utc).isoformat(),
            "granted_at": consent_dir.granted_at.isoformat() if consent_dir.granted_at else datetime.now(timezone.utc).isoformat(),
            "expires_at": consent_dir.expires_at.isoformat() if consent_dir.expires_at else None,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/consent/{consent_id}")
async def get_consent_record_endpoint(
    consent_id: str,
    current_user: TokenPayload = Depends(require_roles([UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN, UserRole.CLINICIAN]))
):
    """Retrieves an existing consent directive record (TC-CON-001)."""
    from services.store import store
    consent = store.get_consent(consent_id)
    if not consent:
        from packages.clinical_models.consent import consent_manager
        hc = consent_manager.view(consent_id)
        if hc:
            return hc.model_dump()
        raise HTTPException(status_code=404, detail=f"Consent record '{consent_id}' not found.")
    return consent.to_dict() if hasattr(consent, "to_dict") else vars(consent)


@router.get("/consent-notice")
async def get_health_worker_consent_notice():
    """Presents the applicable clinical screening informed consent notice (SYS-CON-001)."""
    from services.identity.privacy_router import CONSENT_NOTICES
    return CONSENT_NOTICES.get("SYS-CON-001")


@router.post("/screening")
async def record_screening_endpoint(
    payload: Dict[str, Any],
    current_user: TokenPayload = Depends(require_roles([UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN]))
):
    """Evaluates dynamic questionnaire responses and vital measurements, generating LOINC observations."""
    try:
        return health_worker_service.record_screening(current_user, payload)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/risk-assessment/{citizen_id}")
async def generate_risk_and_action(
    citizen_id: str,
    current_user: TokenPayload = Depends(require_roles([UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN, UserRole.CLINICIAN]))
):
    """Generates AI risk assessment and actionable next step recommendations for frontline worker."""
    try:
        return health_worker_service.generate_risk_and_recommendation(current_user, citizen_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/refer", response_model=ReferralRecord)
async def refer_citizen_endpoint(
    payload: ReferralCreatePayload,
    current_user: TokenPayload = Depends(require_roles([UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN]))
):
    """Issues a formal clinical referral slip to a PHC/CHC/District Hospital and enqueues triage if urgent."""
    try:
        return health_worker_service.create_referral(current_user, payload)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/followup", response_model=FollowUpRecord)
async def schedule_followup_endpoint(
    payload: FollowUpCreatePayload,
    current_user: TokenPayload = Depends(require_roles([UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN]))
):
    """Schedules a home visit or community camp follow-up."""
    try:
        return health_worker_service.create_followup(current_user, payload)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/followups", response_model=List[FollowUpRecord])
async def list_followups_endpoint(
    status: Optional[str] = None,
    current_user: TokenPayload = Depends(require_roles([UserRole.HEALTH_WORKER, UserRole.SYSTEM_ADMIN]))
):
    """Retrieves all scheduled follow-ups for the health worker."""
    return health_worker_service.list_followups(current_user, status_filter=status)
