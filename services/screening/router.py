from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from pydantic import BaseModel
import uuid

from packages.clinical_models.screening import (
    ScreeningSession,
    ScreeningSessionCreate,
)
from packages.clinical_models.observations import Observation
from packages.clinical_models.terminology import LOINC_CODES
from packages.clinical_models.unit_normalization import UnitNormalizer, NormalizedMeasurement
from packages.auth.jwt import (
    get_current_user_token,
    get_current_user_token_optional,
    TokenPayload,
)
from packages.auth.access_control import HealthcareAuthorizationEngine
from packages.types.enums import AuditAction, UserRole
from packages.observability.audit import audit_logger
from services.screening.calculator import calculate_idrs, calculate_cbac, calculate_bmi
from services.screening.questionnaire_models import (
    QuestionnaireWorkflowProfile,
    ConfigurableScreeningSubmission,
    ScreeningEvaluationResponse,
    ExplainableScreeningSummary,
)
from services.screening.questionnaire_engine import QuestionnaireEngine, WORKFLOW_PROFILES
from services.store import store
from packages.security.xss_guard import XSSSanitizer

router = APIRouter(prefix="/screening", tags=["Preventive Screening Engine"])


@router.get("/questionnaire/config", response_model=QuestionnaireWorkflowProfile)
async def get_questionnaire_config(
    workflow: str = "mvp_comprehensive"
):
    """Returns the dynamic, configurable questionnaire protocol definition.
    Allows frontend clients to render form controls without hardcoding fields into UI code.
    """
    return QuestionnaireEngine.get_profile(workflow)


@router.post("/evaluate-configurable", response_model=ScreeningEvaluationResponse)
async def evaluate_configurable_screening(
    submission: ConfigurableScreeningSubmission,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """Processes dynamic questionnaire answers, generates structured LOINC observations,
    triggers NCD risk evaluation, and generates an explainable summary.
    """
    try:
        result = QuestionnaireEngine.evaluate_submission(
            submission=submission,
            actor_id=current_user.sub,
            actor_role=current_user.role,
        )
        return result
    except ValueError as e:
        err_msg = str(e)
        if "not found" in err_msg.lower():
            raise HTTPException(status_code=404, detail=err_msg)
        if "missing required" in err_msg.lower() or "invalid" in err_msg.lower() or "rejected" in err_msg.lower():
            raise HTTPException(status_code=422, detail=err_msg)
        raise HTTPException(status_code=400, detail=err_msg)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Screening evaluation failed: {str(e)}")


@router.get("/evaluate-demo/{scenario}", response_model=ScreeningEvaluationResponse, include_in_schema=False)
async def evaluate_demo_screening(
    scenario: str = "rajesh_kumar_asha",
    current_user: TokenPayload = Depends(get_current_user_token_optional)
):
    """Executes a 5-10 minute demonstration screening scenario with realistic synthetic personas:
    - rajesh_kumar_asha: 51M, ASHA field camp, prediabetic with central adiposity
    - sunita_sharma_citizen: 44F, citizen self-check, elevated vascular strain
    - vikram_singh_clinic: 54M, comprehensive PHC clinic intake with lipid profile
    """
    # Pick or ensure demo citizen exists
    target_citizen_id = "citizen-ramesh-patel-01"
    citizen = store.get_citizen(target_citizen_id)
    if not citizen:
        # Fallback to first citizen in store
        all_cit = store.list_citizens("tenant-karnataka-moh")
        if all_cit:
            target_citizen_id = all_cit[0].id

    demo_answers: Dict[str, Any] = {}
    if scenario == "rajesh_kumar_asha":
        demo_answers = {
            "age": 51,
            "sex": "MALE",
            "height": 168.0,
            "weight": 78.5,
            "waist_circumference": 98.0,
            "systolic_bp": 142.0,
            "diastolic_bp": 90.0,
            "heart_rate": 78.0,
            "fasting_glucose": 118.0,
            "hba1c": 6.2,
            "physical_activity": "Sedentary",
            "diet_quality": "High refined carbs / sweets / fried foods",
            "sleep_hours": 5.5,
            "smoking": "CURRENT_SMOKER",
            "alcohol": "NONE",
            "stress_level": 7.0,
            "family_history": ["DIABETES_ONE_PARENT", "HYPERTENSION"],
            "known_conditions": ["NONE"],
            "medication_history": ["NONE"],
        }
    elif scenario == "sunita_sharma_citizen":
        demo_answers = {
            "age": 44,
            "sex": "FEMALE",
            "height": 158.0,
            "weight": 68.0,
            "waist_circumference": 86.0,
            "systolic_bp": 136.0,
            "diastolic_bp": 88.0,
            "heart_rate": 72.0,
            "fasting_glucose": 104.0,
            "hba1c": 5.8,
            "physical_activity": "Moderate",
            "diet_quality": "Moderate mixed diet",
            "sleep_hours": 6.5,
            "smoking": "NEVER",
            "alcohol": "NONE",
            "stress_level": 8.0,
            "family_history": ["HYPERTENSION"],
            "known_conditions": ["NONE"],
            "medication_history": ["NONE"],
        }
    else:  # vikram_singh_clinic
        demo_answers = {
            "age": 54,
            "sex": "MALE",
            "height": 172.0,
            "weight": 84.0,
            "waist_circumference": 102.0,
            "systolic_bp": 148.0,
            "diastolic_bp": 94.0,
            "heart_rate": 82.0,
            "fasting_glucose": 124.0,
            "hba1c": 6.4,
            "total_cholesterol": 240.0,
            "ldl_cholesterol": 152.0,
            "hdl_cholesterol": 38.0,
            "triglycerides": 210.0,
            "serum_creatinine": 1.15,
            "physical_activity": "None",
            "diet_quality": "High refined carbs / sweets / fried foods",
            "sleep_hours": 5.0,
            "smoking": "CURRENT_SMOKER",
            "alcohol": "REGULAR",
            "stress_level": 8.0,
            "family_history": ["DIABETES_BOTH_PARENTS", "PREMATURE_CAD"],
            "known_conditions": ["DYSLIPIDEMIA"],
            "medication_history": ["AYURVEDIC_HERBAL"],
        }

    submission = ConfigurableScreeningSubmission(
        citizen_id=target_citizen_id,
        workflow_id="mvp_comprehensive",
        answers=demo_answers,
        notes=f"5-Minute Guided Demo Screening Scenario: {scenario}",
        screening_location="DEMO_FIELD_PORTAL",
    )

    return QuestionnaireEngine.evaluate_submission(
        submission=submission,
        actor_id=current_user.sub,
        actor_role=current_user.role,
    )


@router.post("/", response_model=ScreeningSession)
async def submit_screening(
    payload: ScreeningSessionCreate,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    citizen = store.get_citizen(payload.citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    # Validate that at least one screening assessment component is present
    if payload.cbac is None and payload.idrs is None and payload.vitals is None:
        raise HTTPException(
            status_code=422,
            detail="Missing required screening data: must provide at least vitals, CBAC survey, or IDRS survey to complete a screening."
        )

    idrs_score = calculate_idrs(payload.idrs) if payload.idrs else None
    cbac_score = calculate_cbac(payload.cbac) if payload.cbac else None

    session = ScreeningSession(
        id=str(uuid.uuid4()),
        tenant_id=citizen.tenant_id,
        citizen_id=payload.citizen_id,
        conducted_by_id=current_user.sub,
        conducted_at=datetime.now(timezone.utc),
        cbac=payload.cbac,
        idrs=payload.idrs,
        vitals=payload.vitals,
        notes=XSSSanitizer.sanitize_text(payload.notes) if payload.notes else None,
        calculated_idrs_score=idrs_score,
        calculated_cbac_score=cbac_score,
        status=payload.status or "COMPLETED",
        questionnaire_version=payload.questionnaire_version or "1.0.0",
    )
    try:
        store.add_screening(session)
    except ConnectionError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Database service temporarily unavailable (TC-REL-001): {str(e)}",
        )

    # Ingest discrete observations into LOINC store
    if payload.vitals:
        v = payload.vitals
        vital_mapping = [
            ("SYSTOLIC_BP", v.systolic_bp),
            ("DIASTOLIC_BP", v.diastolic_bp),
            ("HEART_RATE", v.heart_rate),
            ("FASTING_GLUCOSE", v.fasting_glucose),
            ("HBA1C", v.hba1c),
            ("TOTAL_CHOLESTEROL", v.total_cholesterol),
            ("HDL_CHOLESTEROL", v.hdl_cholesterol),
            ("TRIGLYCERIDES", v.triglycerides),
            ("BMI", v.bmi),
            ("WAIST_CIRCUMFERENCE", v.waist_circumference),
            ("EGFR", v.egfr),
        ]
        for key, val in vital_mapping:
            if val is not None and key in LOINC_CODES:
                meta = LOINC_CODES[key]
                obs = Observation(
                    id=str(uuid.uuid4()),
                    tenant_id=citizen.tenant_id,
                    citizen_id=citizen.id,
                    code=key,
                    value=float(val),
                    unit=meta.get("unit", ""),
                    loinc_code=meta["code"],
                    display_name=meta["display"],
                    source="SCREENING_SESSION",
                )
                try:
                    store.add_observation(obs)
                except ConnectionError as e:
                    raise HTTPException(
                        status_code=503,
                        detail=f"Database service temporarily unavailable (TC-REL-001): {str(e)}",
                    )

    audit_logger.record(
        tenant_id=citizen.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.SCREENING_SUBMITTED,
        resource_type="ScreeningSession",
        resource_id=session.id,
    )

    return session


@router.get("/history/{citizen_id}", response_model=List[ScreeningSession])
async def get_screening_history(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)
    return store.get_citizen_screenings(citizen_id)


# ==============================================================================
# UNIT NORMALIZATION & EQUIVALENCE ENDPOINTS (TC-SCR-004)
# ==============================================================================

class NormalizeMeasurementRequest(BaseModel):
    metric: str
    value: float
    unit: str


class CompareMeasurementsRequest(BaseModel):
    metric: str
    value1: float
    unit1: str
    value2: float
    unit2: str
    tolerance: Optional[float] = 0.5


class CompareMeasurementsResponse(BaseModel):
    equivalent: bool
    metric: str
    normalized_value1: float
    normalized_value2: float
    canonical_unit: str
    tolerance_applied: float


@router.post("/normalize-measurement", response_model=NormalizedMeasurement)
async def normalize_measurement_endpoint(payload: NormalizeMeasurementRequest):
    """Converts a clinical measurement from any supported unit to canonical standard unit."""
    try:
        return UnitNormalizer.normalize(payload.metric, payload.value, payload.unit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/compare-measurements", response_model=CompareMeasurementsResponse)
async def compare_measurements_endpoint(payload: CompareMeasurementsRequest):
    """Compares two measurements in different supported units and evaluates equivalence within rounding tolerance."""
    try:
        equiv, n1, n2 = UnitNormalizer.are_equivalent(
            metric=payload.metric,
            val1=payload.value1,
            unit1=payload.unit1,
            val2=payload.value2,
            unit2=payload.unit2,
            rounding_tolerance=payload.tolerance or 0.5,
        )
        norm_res = UnitNormalizer.normalize(payload.metric, payload.value1, payload.unit1)
        return CompareMeasurementsResponse(
            equivalent=equiv,
            metric=payload.metric.upper(),
            normalized_value1=n1,
            normalized_value2=n2,
            canonical_unit=norm_res.canonical_unit,
            tolerance_applied=payload.tolerance or 0.5,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==============================================================================
# BMI CALCULATION & CLINICAL OBSERVATIONS SUITE (TC-SCR-005 / 006 / 007 / 008)
# ==============================================================================

class BMICalculationRequest(BaseModel):
    citizen_id: Optional[str] = None
    height: float
    weight: float
    height_unit: str = "m"
    weight_unit: str = "kg"
    rounding_decimals: int = 2
    store_result: bool = True
    recorded_at: Optional[datetime] = None
    source: str = "SCREENING_SESSION"


class BMICalculationResponse(BaseModel):
    citizen_id: Optional[str]
    height_input: float
    height_unit: str
    height_m: float
    weight_input: float
    weight_unit: str
    weight_kg: float
    bmi: float
    raw_bmi: float
    unit: str
    category: str
    risk_tier: str
    rounding_rule: str
    formula: str
    stored_observation_ids: List[str] = []


@router.post("/bmi/calculate", response_model=BMICalculationResponse)
async def calculate_and_store_bmi_endpoint(
    payload: BMICalculationRequest,
):
    """Calculates BMI from height & weight and stores discrete observations (TC-SCR-005).
    Formula: BMI = weight in kilograms ÷ (height in metres)².
    """
    try:
        res = calculate_bmi(
            height=payload.height,
            weight=payload.weight,
            height_unit=payload.height_unit,
            weight_unit=payload.weight_unit,
            rounding_decimals=payload.rounding_decimals,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    cid = payload.citizen_id or "citizen-synth-test-01"
    now = payload.recorded_at or datetime.now(timezone.utc)
    stored_ids: List[str] = []

    if payload.store_result:
        # Ensure citizen exists in store or register synthetic
        if not store.get_citizen(cid):
            from services.store import CitizenRecord
            store.add_citizen(CitizenRecord(
                id=cid,
                tenant_id="tenant-karnataka-moh",
                abha_id="91-0000-1111-2222",
                first_name="Synthetic",
                last_name="Citizen",
                birth_date=datetime(1985, 1, 1),
                gender="MALE",
                phone="+919800000000",
                state="Karnataka",
                district="Bengaluru Urban",
                sub_district="South",
                village_or_ward="Ward 10",
            ))

        # 1. Height Observation
        h_obs = Observation(
            citizen_id=cid,
            code="HEIGHT",
            value=res["height_m"] * 100.0,
            unit="cm",
            loinc_code="8302-2",
            display_name="Body height",
            source=payload.source,
            recorded_at=now,
        )
        store.add_observation(h_obs)
        stored_ids.append(h_obs.id)

        # 2. Weight Observation
        w_obs = Observation(
            citizen_id=cid,
            code="WEIGHT",
            value=res["weight_kg"],
            unit="kg",
            loinc_code="29463-7",
            display_name="Body weight",
            source=payload.source,
            recorded_at=now,
        )
        store.add_observation(w_obs)
        stored_ids.append(w_obs.id)

        # 3. BMI Observation
        bmi_obs = Observation(
            citizen_id=cid,
            code="BMI",
            value=res["bmi"],
            unit="kg/m2",
            loinc_code="39156-5",
            display_name="Body mass index (BMI) [Ratio]",
            source=payload.source,
            recorded_at=now,
        )
        store.add_observation(bmi_obs)
        stored_ids.append(bmi_obs.id)

    return BMICalculationResponse(
        citizen_id=cid,
        height_input=res["height_input"],
        height_unit=res["height_unit"],
        height_m=res["height_m"],
        weight_input=res["weight_input"],
        weight_unit=res["weight_unit"],
        weight_kg=res["weight_kg"],
        bmi=res["bmi"],
        raw_bmi=res["raw_bmi"],
        unit=res["unit"],
        category=res["category"],
        risk_tier=res["risk_tier"],
        rounding_rule=res["rounding_rule"],
        formula=res["formula"],
        stored_observation_ids=stored_ids,
    )


@router.get("/bmi/{citizen_id}")
async def get_stored_bmi_endpoint(citizen_id: str):
    """Retrieves stored BMI observations for citizen (TC-SCR-005)."""
    obs_list = store.get_citizen_observations(citizen_id)
    bmi_obs = [o for o in obs_list if o.code.upper() == "BMI"]
    if not bmi_obs:
        raise HTTPException(status_code=404, detail=f"No stored BMI observations found for citizen {citizen_id}")

    latest_bmi = bmi_obs[-1]
    return {
        "citizen_id": citizen_id,
        "latest_bmi": latest_bmi.to_dict(),
        "total_bmi_records": len(bmi_obs),
        "history": [b.to_dict() for b in bmi_obs],
    }


class ObservationIngestPayload(BaseModel):
    citizen_id: str
    code: str
    value: float
    unit: str
    recorded_at: Optional[datetime] = None
    source: str = "MANUAL_ENTRY"
    source_event_id: Optional[str] = None
    provenance: Optional[Dict[str, Any]] = None


@router.post("/observations")
async def ingest_observation_endpoint(payload: ObservationIngestPayload):
    """Ingests discrete clinical observation with idempotency (TC-SCR-006) and conflict detection (TC-SCR-007)."""
    # Auto-resolve LOINC metadata
    loinc_c = ""
    disp_name = payload.code
    if payload.code.upper() in LOINC_CODES:
        meta = LOINC_CODES[payload.code.upper()]
        loinc_c = meta.get("code", "")
        disp_name = meta.get("display", payload.code)
    else:
        # Check reverse lookup by LOINC code (e.g. "8480-6")
        for k, meta in LOINC_CODES.items():
            if meta.get("code") == payload.code:
                loinc_c = meta.get("code", "")
                disp_name = meta.get("display", k)
                break

    now = payload.recorded_at or datetime.now(timezone.utc)
    new_obs = Observation(
        citizen_id=payload.citizen_id,
        code=payload.code.upper(),
        value=payload.value,
        unit=payload.unit,
        source=payload.source,
        source_event_id=payload.source_event_id,
        provenance=payload.provenance or {"ingested_via": "POST /observations", "timestamp": datetime.now(timezone.utc).isoformat()},
        loinc_code=loinc_c,
        display_name=disp_name,
        recorded_at=now,
    )

    count_before = len(store.get_citizen_observations(payload.citizen_id, sort_chronological=False))
    persisted = store.add_observation(new_obs)
    count_after = len(store.get_citizen_observations(payload.citizen_id, sort_chronological=False))

    is_duplicate_suppressed = (count_before == count_after and persisted.id != new_obs.id)

    return {
        "status": "DEDUPLICATED" if is_duplicate_suppressed else "INGESTED",
        "idempotent_replay": is_duplicate_suppressed,
        "has_conflict": getattr(persisted, "has_conflict", False),
        "conflict_details": getattr(persisted, "conflict_details", None),
        "observation": persisted.to_dict(),
        "total_stored_count": count_after,
    }


@router.get("/observations/{citizen_id}")
async def get_observations_endpoint(citizen_id: str, code: Optional[str] = None):
    """Retrieves stored observations in chronological order (TC-SCR-008)."""
    all_obs = store.get_citizen_observations(citizen_id, sort_chronological=True)
    if code:
        all_obs = [
            o for o in all_obs
            if o.code.upper() == code.upper() or getattr(o, "loinc_code", "") == code
        ]
    return [o.to_dict() for o in all_obs]


@router.get("/observations/{citizen_id}/summary")
async def get_observation_summary_endpoint(citizen_id: str, code: Optional[str] = None):
    """Requests latest measurement summary applying documented selection/review policy (TC-SCR-007)."""
    return store.get_latest_observation_summary(citizen_id, code)



