"""REST API Router for Multi-EMR Clinical Interoperability.

Exposes normalized clinical data, federated charts, and audit trails.
Enforces RBAC and query injection defense.
"""

from typing import Dict, List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
import structlog

from packages.types.enums import UserRole
from packages.auth.jwt import get_current_user_token, TokenPayload
from packages.interop.models import (
    NormalizedPatient,
    NormalizedObservation,
    NormalizedCondition,
    NormalizedMedication,
    NormalizedEncounter,
    NormalizedCarePlan,
    NormalizedClinicalDocument,
    ObservationCategory,
    FederatedClinicalChart,
)
from packages.interop.base import AccessContext
from packages.interop.service import federated_clinical_data_provider
from packages.interop.security import (
    AccessDeniedError,
    ArbitraryQueryViolationError,
)
from packages.interop.audit import ClinicalAccessAuditLogger

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/interop", tags=["Clinical Interoperability & EMR Federation"])

from services.clinical.interop_test_router import test_router
router.include_router(test_router, prefix="/test", tags=["Section 14 Test Cases: Integration & Wearables"])


def _extract_access_context(
    request: Request,
    current_user: TokenPayload,
    purpose: str = "CARE_DELIVERY",
) -> AccessContext:
    role = current_user.role if isinstance(current_user.role, UserRole) else UserRole(str(current_user.role))
    return AccessContext(
        actor_id=current_user.sub,
        actor_role=role,
        tenant_id=current_user.tenant_id,
        purpose_of_use=purpose,
        client_ip=request.client.host if request.client else None,
        is_ai_agent=False,
    )


@router.get("/adapters", summary="Check health and connectivity of all clinical data adapters")
async def get_adapters_health():
    """Returns connectivity and diagnostic status for SevaHealth Local, openEHR, bezs-emr-gql, and bezs-hms."""
    return await federated_clinical_data_provider.health_check()


@router.get("/patient/{patient_id}", response_model=NormalizedPatient, summary="Get federated patient demographics")
async def get_patient_record(
    patient_id: str,
    request: Request,
    purpose: str = Query("CARE_DELIVERY"),
    current_user: TokenPayload = Depends(get_current_user_token),
):
    ctx = _extract_access_context(request, current_user, purpose)
    try:
        patient = await federated_clinical_data_provider.get_patient(patient_id, ctx)
        if not patient:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found in any clinical repository")
        return patient
    except AccessDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ArbitraryQueryViolationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/patient/{patient_id}/observations", response_model=List[NormalizedObservation], summary="Get federated observations")
async def get_patient_observations(
    patient_id: str,
    request: Request,
    category: Optional[ObservationCategory] = None,
    purpose: str = Query("CARE_DELIVERY"),
    current_user: TokenPayload = Depends(get_current_user_token),
):
    ctx = _extract_access_context(request, current_user, purpose)
    try:
        return await federated_clinical_data_provider.get_observations(patient_id, ctx, category=category)
    except AccessDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ArbitraryQueryViolationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/patient/{patient_id}/conditions", response_model=List[NormalizedCondition], summary="Get federated conditions")
async def get_patient_conditions(
    patient_id: str,
    request: Request,
    purpose: str = Query("CARE_DELIVERY"),
    current_user: TokenPayload = Depends(get_current_user_token),
):
    ctx = _extract_access_context(request, current_user, purpose)
    try:
        return await federated_clinical_data_provider.get_conditions(patient_id, ctx)
    except AccessDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ArbitraryQueryViolationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/patient/{patient_id}/medications", response_model=List[NormalizedMedication], summary="Get federated medications")
async def get_patient_medications(
    patient_id: str,
    request: Request,
    purpose: str = Query("CARE_DELIVERY"),
    current_user: TokenPayload = Depends(get_current_user_token),
):
    ctx = _extract_access_context(request, current_user, purpose)
    try:
        return await federated_clinical_data_provider.get_medications(patient_id, ctx)
    except AccessDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ArbitraryQueryViolationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/patient/{patient_id}/encounters", response_model=List[NormalizedEncounter], summary="Get federated encounters")
async def get_patient_encounters(
    patient_id: str,
    request: Request,
    purpose: str = Query("CARE_DELIVERY"),
    current_user: TokenPayload = Depends(get_current_user_token),
):
    ctx = _extract_access_context(request, current_user, purpose)
    try:
        return await federated_clinical_data_provider.get_encounters(patient_id, ctx)
    except AccessDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ArbitraryQueryViolationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/patient/{patient_id}/care-plans", response_model=List[NormalizedCarePlan], summary="Get federated care plans")
async def get_patient_care_plans(
    patient_id: str,
    request: Request,
    purpose: str = Query("CARE_DELIVERY"),
    current_user: TokenPayload = Depends(get_current_user_token),
):
    ctx = _extract_access_context(request, current_user, purpose)
    try:
        return await federated_clinical_data_provider.get_care_plans(patient_id, ctx)
    except AccessDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ArbitraryQueryViolationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/patient/{patient_id}/documents", response_model=List[NormalizedClinicalDocument], summary="Get federated clinical documents")
async def get_patient_documents(
    patient_id: str,
    request: Request,
    purpose: str = Query("CARE_DELIVERY"),
    current_user: TokenPayload = Depends(get_current_user_token),
):
    ctx = _extract_access_context(request, current_user, purpose)
    try:
        return await federated_clinical_data_provider.get_clinical_documents(patient_id, ctx)
    except AccessDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ArbitraryQueryViolationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/patient/{patient_id}/chart", response_model=FederatedClinicalChart, summary="Get full federated longitudinal chart")
async def get_patient_federated_chart(
    patient_id: str,
    request: Request,
    purpose: str = Query("CARE_DELIVERY"),
    current_user: TokenPayload = Depends(get_current_user_token),
):
    ctx = _extract_access_context(request, current_user, purpose)
    try:
        return await federated_clinical_data_provider.get_federated_chart(patient_id, ctx)
    except AccessDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ArbitraryQueryViolationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/audit-logs", summary="Get clinical access audit logs for compliance")
async def get_clinical_audit_logs(
    patient_id: Optional[str] = Query(None),
    actor_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    current_user: TokenPayload = Depends(get_current_user_token),
):
    # Only CLINICIAN, SYSTEM_ADMIN, or auditor can access audit logs
    if current_user.role not in [UserRole.SYSTEM_ADMIN, UserRole.CLINICIAN]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to audit trail")

    return ClinicalAccessAuditLogger.get_audit_trail(
        patient_id=patient_id,
        actor_id=actor_id,
        limit=limit,
    )
