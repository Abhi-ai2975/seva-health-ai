from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends

from packages.clinical_models.fhir import (
    export_patient_fhir_r4,
    export_observation_fhir_r4,
    export_risk_assessment_fhir_r4,
)
from packages.auth.jwt import get_current_user_token, TokenPayload
from packages.auth.access_control import HealthcareAuthorizationEngine
from services.store import store

router = APIRouter(prefix="/fhir", tags=["HL7 FHIR R4 Interoperability (ABDM Aligned)"])


@router.get("/Patient/{citizen_id}")
async def get_fhir_patient(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    c = store.get_citizen(citizen_id)
    if not c:
        raise HTTPException(status_code=404, detail="Patient not found")

    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, c)

    return export_patient_fhir_r4(
        citizen_id=c.id,
        first_name=c.first_name,
        last_name=c.last_name,
        gender=c.gender.value,
        birth_date=c.birth_date,
        phone=c.phone,
        abha_id=c.abha_id,
    )


@router.get("/Observation")
async def get_fhir_observations(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    c = store.get_citizen(citizen_id)
    if not c:
        raise HTTPException(status_code=404, detail="Patient not found")

    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, c)

    obs_list = store.get_citizen_observations(citizen_id)
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(obs_list),
        "entry": [{"resource": export_observation_fhir_r4(o)} for o in obs_list]
    }


@router.get("/RiskAssessment/{citizen_id}")
async def get_fhir_risk_assessment(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    c = store.get_citizen(citizen_id)
    if not c:
        raise HTTPException(status_code=404, detail="Patient not found")

    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, c)

    risk = store.get_latest_risk(citizen_id)
    if not risk:
        raise HTTPException(status_code=404, detail="Risk assessment not found")

    return export_risk_assessment_fhir_r4(risk)
