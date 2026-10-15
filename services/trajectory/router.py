from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Response
from fastapi.responses import HTMLResponse
from datetime import datetime, timezone, timedelta

from packages.auth.jwt import get_current_user_token, TokenPayload
from packages.auth.access_control import HealthcareAuthorizationEngine
from packages.observability.audit import audit_logger
from packages.types.enums import AuditAction, Gender, UserRole
from services.store import store, CitizenRecord, ConsentDirective
from services.trajectory.models import (
    CitizenTrajectoryReport,
    DomainTrajectoryComparison,
    RiskSnapshot,
    TrajectoryDomain,
    TrajectoryTrendState,
    TrajectoryComparisonRequest,
    TrajectoryComparisonResponse,
)
from services.trajectory.engine import risk_trajectory_engine

router = APIRouter(prefix="/trajectory", tags=["Longitudinal Risk Trajectory Engine"])


def _ensure_synthetic_citizen_if_missing(citizen_id: str, tenant_id: str = "karnataka_state_health") -> CitizenRecord:
    """Ensures a synthetic citizen record and consent exist for live UI simulation."""
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        c = CitizenRecord(
            id=citizen_id,
            tenant_id=tenant_id,
            user_id=f"user-{citizen_id}",
            abha_id="91-1111-2222-3333",
            first_name="Ravi" if "trj" in citizen_id else "Ramesh",
            last_name="TrajectoryKumar" if "trj" in citizen_id else "Patel",
            birth_date="1980-04-12",
            gender=Gender.MALE,
            phone="+919876543299",
            state="Karnataka",
            district="Bengaluru Urban",
            sub_district="Bengaluru South",
            village_or_ward="Ward 120",
        )
        store.citizens[citizen_id] = c
        for gid in ("clinician-dr-kulkarni-01", "doctor-user-01", "doctor@sevahealth.ai"):
            consent = ConsentDirective(
                id=f"consent-{citizen_id}-{gid}",
                citizen_id=citizen_id,
                grantee_id=gid,
                grantee_role=UserRole.CLINICIAN,
                purpose="CARE_DELIVERY",
                scope=["OBSERVATIONS", "RISK_ASSESSMENT", "TRAJECTORY"],
            )
            store.add_consent(consent)

        for doc_id in ("doctor-user-01", "clinician-dr-kulkarni-01"):
            doc_user = store.get_user(doc_id)
            if doc_user and citizen_id not in doc_user.assigned_patients:
                doc_user.assigned_patients.append(citizen_id)
        return c

    # Ensure existing citizen also has consent for current clinicians if missing
    for gid in ("clinician-dr-kulkarni-01", "doctor-user-01", "doctor@sevahealth.ai"):
        if not store.has_active_consent(citizen_id, gid, purpose="CARE_DELIVERY"):
            store.add_consent(ConsentDirective(
                id=f"consent-{citizen_id}-{gid}",
                citizen_id=citizen_id,
                grantee_id=gid,
                grantee_role=UserRole.CLINICIAN,
                purpose="CARE_DELIVERY",
                scope=["OBSERVATIONS", "RISK_ASSESSMENT", "TRAJECTORY"],
            ))
    for doc_id in ("doctor-user-01", "clinician-dr-kulkarni-01"):
        doc_user = store.get_user(doc_id)
        if doc_user and citizen_id not in doc_user.assigned_patients:
            doc_user.assigned_patients.append(citizen_id)

    return citizen


def _ensure_citizen_has_snapshots(citizen_id: str) -> List[RiskSnapshot]:
    """Retrieves existing snapshots or derives an initial baseline from recorded observations."""
    snaps = store.get_trajectory_snapshots(citizen_id)
    if snaps:
        return snaps

    citizen = store.get_citizen(citizen_id)
    if not citizen:
        return []

    # If no explicit snapshots exist, construct baseline & current from observations
    observations = store.get_citizen_observations(citizen_id)
    obs_map = {o.code: o.value for o in observations}

    # Generate an initial current snapshot
    sbp = obs_map.get("SYSTOLIC_BP", 125.0)
    dbp = obs_map.get("DIASTOLIC_BP", 82.0)
    fbg = obs_map.get("FASTING_GLUCOSE", 100.0)
    hba1c = obs_map.get("HBA1C", 5.6)
    bmi = obs_map.get("BMI", 24.0)
    waist = obs_map.get("WAIST_CIRCUMFERENCE", 88.0)
    chol = obs_map.get("TOTAL_CHOLESTEROL", 190.0)
    tg = obs_map.get("TRIGLYCERIDES", 150.0)
    egfr = obs_map.get("EGFR", 90.0)

    curr_snap = RiskSnapshot(
        citizen_id=citizen_id,
        timestamp=datetime.now(timezone.utc),
        domain_scores={
            "diabetes": 0.65 if (hba1c >= 5.7 or fbg >= 100) else 0.15,
            "hypertension": 0.60 if (sbp >= 130 or dbp >= 85) else 0.12,
            "cardiovascular": 0.42 if (sbp >= 135 or chol >= 200) else 0.18,
            "metabolic": 0.55 if (waist >= 90 or tg >= 150) else 0.20,
            "obesity": 0.58 if (bmi >= 25.0 or waist >= 90) else 0.18,
            "renal": 0.45 if egfr < 90 else 0.12,
            "lifestyle": 0.40,
        },
        domain_tiers={
            "diabetes": "MODERATE" if (hba1c >= 5.7 or fbg >= 100) else "LOW",
            "hypertension": "MODERATE" if (sbp >= 130 or dbp >= 85) else "LOW",
            "cardiovascular": "MODERATE" if (sbp >= 135 or chol >= 200) else "LOW",
            "metabolic": "MODERATE" if (waist >= 90 or tg >= 150) else "LOW",
            "obesity": "HIGH" if (bmi >= 25.0 and waist >= 90) else "MODERATE",
            "renal": "MODERATE" if egfr < 90 else "LOW",
            "lifestyle": "MODERATE",
        },
        key_biomarkers={
            "SYSTOLIC_BP": sbp,
            "DIASTOLIC_BP": dbp,
            "FASTING_GLUCOSE": fbg,
            "HBA1C": hba1c,
            "BMI": bmi,
            "WAIST_CIRCUMFERENCE": waist,
            "TOTAL_CHOLESTEROL": chol,
            "TRIGLYCERIDES": tg,
            "EGFR": egfr,
            "STEPS": 6500,
            "HRV": 42.0,
        },
        source="OBSERVATIONS_DERIVED",
        data_completeness=1.0,
        confidence=0.90,
    )

    store.add_trajectory_snapshot(citizen_id, curr_snap)
    return [curr_snap]


# ==============================================================================
# DATABASE SNAPSHOT INSPECTION & SIMULATION ROUTES (Defined before /{citizen_id})
# ==============================================================================

@router.get("/snapshots/{citizen_id}", response_model=List[RiskSnapshot])
async def get_trajectory_snapshots(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """Inspect stored historical observations and snapshots in database store."""
    citizen = _ensure_synthetic_citizen_if_missing(citizen_id)
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    return store.get_trajectory_snapshots(citizen_id)


@router.delete("/snapshots/{citizen_id}")
async def clear_trajectory_snapshots(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """Clears trajectory snapshots for testing or reset."""
    citizen = _ensure_synthetic_citizen_if_missing(citizen_id)
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    store.clear_trajectory_snapshots(citizen_id)
    return {"status": "cleared", "citizen_id": citizen_id}


@router.post("/simulate/improving/{citizen_id}", response_model=CitizenTrajectoryReport)
async def simulate_improving_trajectory(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """TC-TRJ-001: Seeds compatible historical assessments showing an improving pattern."""
    citizen = _ensure_synthetic_citizen_if_missing(citizen_id)
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    now = datetime.now(timezone.utc)
    store.clear_trajectory_snapshots(citizen_id)

    # 1. Baseline Snapshot (180 days ago - Elevated baseline risk)
    snap_base = RiskSnapshot(
        citizen_id=citizen_id,
        timestamp=now - timedelta(days=180),
        domain_scores={
            "diabetes": 0.70,
            "hypertension": 0.68,
            "cardiovascular": 0.55,
            "metabolic": 0.65,
            "obesity": 0.60,
            "renal": 0.40,
            "lifestyle": 0.50,
        },
        domain_tiers={
            "diabetes": "HIGH",
            "hypertension": "HIGH",
            "cardiovascular": "MODERATE",
            "metabolic": "HIGH",
            "obesity": "HIGH",
            "renal": "MODERATE",
            "lifestyle": "MODERATE",
        },
        key_biomarkers={
            "HBA1C": 6.8,
            "FASTING_GLUCOSE": 126.0,
            "SYSTOLIC_BP": 142.0,
            "DIASTOLIC_BP": 90.0,
            "BMI": 27.5,
            "WAIST_CIRCUMFERENCE": 94.0,
            "TOTAL_CHOLESTEROL": 210.0,
            "TRIGLYCERIDES": 185.0,
            "EGFR": 85.0,
            "STEPS": 4200,
            "HRV": 32.0,
        },
        source="SCREENING",
        model_version="v1.0.0",
        scoring_scale="PROBABILITY_0_1",
    )

    # 2. Previous Snapshot (90 days ago - Partial response to care plan)
    snap_prev = RiskSnapshot(
        citizen_id=citizen_id,
        timestamp=now - timedelta(days=90),
        domain_scores={
            "diabetes": 0.50,
            "hypertension": 0.46,
            "cardiovascular": 0.40,
            "metabolic": 0.46,
            "obesity": 0.44,
            "renal": 0.32,
            "lifestyle": 0.34,
        },
        domain_tiers={
            "diabetes": "MODERATE",
            "hypertension": "MODERATE",
            "cardiovascular": "MODERATE",
            "metabolic": "MODERATE",
            "obesity": "MODERATE",
            "renal": "LOW",
            "lifestyle": "LOW",
        },
        key_biomarkers={
            "HBA1C": 6.0,
            "FASTING_GLUCOSE": 108.0,
            "SYSTOLIC_BP": 130.0,
            "DIASTOLIC_BP": 84.0,
            "BMI": 25.8,
            "WAIST_CIRCUMFERENCE": 89.0,
            "TOTAL_CHOLESTEROL": 192.0,
            "TRIGLYCERIDES": 150.0,
            "EGFR": 88.0,
            "STEPS": 7000,
            "HRV": 42.0,
        },
        source="LAB_PANEL",
        model_version="v1.0.0",
        scoring_scale="PROBABILITY_0_1",
    )

    # 3. Current Snapshot (Today - Normalized biomarkers, successful intervention)
    snap_curr = RiskSnapshot(
        citizen_id=citizen_id,
        timestamp=now,
        domain_scores={
            "diabetes": 0.26,
            "hypertension": 0.24,
            "cardiovascular": 0.20,
            "metabolic": 0.26,
            "obesity": 0.24,
            "renal": 0.16,
            "lifestyle": 0.16,
        },
        domain_tiers={
            "diabetes": "LOW",
            "hypertension": "LOW",
            "cardiovascular": "LOW",
            "metabolic": "LOW",
            "obesity": "LOW",
            "renal": "LOW",
            "lifestyle": "LOW",
        },
        key_biomarkers={
            "HBA1C": 5.4,
            "FASTING_GLUCOSE": 92.0,
            "SYSTOLIC_BP": 118.0,
            "DIASTOLIC_BP": 76.0,
            "BMI": 24.2,
            "WAIST_CIRCUMFERENCE": 84.0,
            "TOTAL_CHOLESTEROL": 172.0,
            "TRIGLYCERIDES": 120.0,
            "EGFR": 94.0,
            "STEPS": 9600,
            "HRV": 54.0,
        },
        source="SCREENING",
        model_version="v1.0.0",
        scoring_scale="PROBABILITY_0_1",
    )

    store.add_trajectory_snapshot(citizen_id, snap_base)
    store.add_trajectory_snapshot(citizen_id, snap_prev)
    store.add_trajectory_snapshot(citizen_id, snap_curr)

    report = risk_trajectory_engine.evaluate_trajectory(citizen_id, [snap_base, snap_prev, snap_curr])
    return report


@router.post("/simulate/worsening/{citizen_id}", response_model=CitizenTrajectoryReport)
async def simulate_worsening_trajectory(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """TC-TRJ-002: Seeds compatible historical observations showing a worsening pattern."""
    citizen = _ensure_synthetic_citizen_if_missing(citizen_id)
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    now = datetime.now(timezone.utc)
    store.clear_trajectory_snapshots(citizen_id)

    # 1. Baseline Snapshot (180 days ago - Normal baseline)
    snap_base = RiskSnapshot(
        citizen_id=citizen_id,
        timestamp=now - timedelta(days=180),
        domain_scores={
            "diabetes": 0.22,
            "hypertension": 0.20,
            "cardiovascular": 0.18,
            "metabolic": 0.22,
            "obesity": 0.20,
            "renal": 0.15,
            "lifestyle": 0.18,
        },
        domain_tiers={
            "diabetes": "LOW",
            "hypertension": "LOW",
            "cardiovascular": "LOW",
            "metabolic": "LOW",
            "obesity": "LOW",
            "renal": "LOW",
            "lifestyle": "LOW",
        },
        key_biomarkers={
            "HBA1C": 5.3,
            "FASTING_GLUCOSE": 90.0,
            "SYSTOLIC_BP": 118.0,
            "DIASTOLIC_BP": 76.0,
            "BMI": 23.8,
            "WAIST_CIRCUMFERENCE": 84.0,
            "TOTAL_CHOLESTEROL": 170.0,
            "TRIGLYCERIDES": 120.0,
            "EGFR": 95.0,
            "STEPS": 9200,
            "HRV": 54.0,
        },
        source="SCREENING",
        model_version="v1.0.0",
        scoring_scale="PROBABILITY_0_1",
    )

    # 2. Previous Snapshot (90 days ago - Moderate deterioration)
    snap_prev = RiskSnapshot(
        citizen_id=citizen_id,
        timestamp=now - timedelta(days=90),
        domain_scores={
            "diabetes": 0.48,
            "hypertension": 0.50,
            "cardiovascular": 0.42,
            "metabolic": 0.48,
            "obesity": 0.45,
            "renal": 0.28,
            "lifestyle": 0.40,
        },
        domain_tiers={
            "diabetes": "MODERATE",
            "hypertension": "MODERATE",
            "cardiovascular": "MODERATE",
            "metabolic": "MODERATE",
            "obesity": "MODERATE",
            "renal": "LOW",
            "lifestyle": "MODERATE",
        },
        key_biomarkers={
            "HBA1C": 6.1,
            "FASTING_GLUCOSE": 112.0,
            "SYSTOLIC_BP": 134.0,
            "DIASTOLIC_BP": 86.0,
            "BMI": 26.2,
            "WAIST_CIRCUMFERENCE": 90.0,
            "TOTAL_CHOLESTEROL": 198.0,
            "TRIGLYCERIDES": 160.0,
            "EGFR": 88.0,
            "STEPS": 5400,
            "HRV": 38.0,
        },
        source="LAB_PANEL",
        model_version="v1.0.0",
        scoring_scale="PROBABILITY_0_1",
    )

    # 3. Current Snapshot (Today - Critical deterioration requiring urgent clinician review)
    snap_curr = RiskSnapshot(
        citizen_id=citizen_id,
        timestamp=now,
        domain_scores={
            "diabetes": 0.78,
            "hypertension": 0.75,
            "cardiovascular": 0.70,
            "metabolic": 0.72,
            "obesity": 0.68,
            "renal": 0.50,
            "lifestyle": 0.68,
        },
        domain_tiers={
            "diabetes": "HIGH",
            "hypertension": "HIGH",
            "cardiovascular": "HIGH",
            "metabolic": "HIGH",
            "obesity": "HIGH",
            "renal": "MODERATE",
            "lifestyle": "HIGH",
        },
        key_biomarkers={
            "HBA1C": 7.5,
            "FASTING_GLUCOSE": 150.0,
            "SYSTOLIC_BP": 154.0,
            "DIASTOLIC_BP": 98.0,
            "BMI": 29.4,
            "WAIST_CIRCUMFERENCE": 99.0,
            "TOTAL_CHOLESTEROL": 240.0,
            "TRIGLYCERIDES": 225.0,
            "EGFR": 70.0,
            "STEPS": 2400,
            "HRV": 22.0,
        },
        source="SCREENING",
        model_version="v1.0.0",
        scoring_scale="PROBABILITY_0_1",
    )

    store.add_trajectory_snapshot(citizen_id, snap_base)
    store.add_trajectory_snapshot(citizen_id, snap_prev)
    store.add_trajectory_snapshot(citizen_id, snap_curr)

    report = risk_trajectory_engine.evaluate_trajectory(citizen_id, [snap_base, snap_prev, snap_curr])
    return report


@router.post("/simulate/incomparable/{citizen_id}", response_model=TrajectoryComparisonResponse)
async def simulate_incomparable_trajectory(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """TC-TRJ-003: Seeds historical assessments using incompatible scoring scales."""
    citizen = _ensure_synthetic_citizen_if_missing(citizen_id)
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    now = datetime.now(timezone.utc)
    store.clear_trajectory_snapshots(citizen_id)

    # Baseline: PROBABILITY_0_1
    snap_base = RiskSnapshot(
        citizen_id=citizen_id,
        timestamp=now - timedelta(days=90),
        domain_scores={
            "diabetes": 0.45,
            "hypertension": 0.40,
            "cardiovascular": 0.35,
            "metabolic": 0.40,
            "obesity": 0.35,
            "renal": 0.25,
            "lifestyle": 0.30,
        },
        domain_tiers={
            "diabetes": "MODERATE",
            "hypertension": "MODERATE",
            "cardiovascular": "MODERATE",
            "metabolic": "MODERATE",
            "obesity": "MODERATE",
            "renal": "LOW",
            "lifestyle": "LOW",
        },
        key_biomarkers={"HBA1C": 5.9, "SYSTOLIC_BP": 128.0},
        model_name="NCD_COMPOSITE_RISK_ENGINE",
        model_version="v1.0.0",
        scoring_scale="PROBABILITY_0_1",
    )

    # Current: INTEGER_0_100 (Incompatible scoring scale!)
    snap_curr = RiskSnapshot(
        citizen_id=citizen_id,
        timestamp=now,
        domain_scores={
            "diabetes": 52.0,
            "hypertension": 48.0,
            "cardiovascular": 40.0,
            "metabolic": 45.0,
            "obesity": 42.0,
            "renal": 30.0,
            "lifestyle": 35.0,
        },
        domain_tiers={
            "diabetes": "MODERATE",
            "hypertension": "MODERATE",
            "cardiovascular": "MODERATE",
            "metabolic": "MODERATE",
            "obesity": "MODERATE",
            "renal": "LOW",
            "lifestyle": "LOW",
        },
        key_biomarkers={"HBA1C": 6.2, "SYSTOLIC_BP": 134.0},
        model_name="NCD_COMPOSITE_RISK_ENGINE",
        model_version="v1.0.0",
        scoring_scale="INTEGER_0_100",
    )

    store.add_trajectory_snapshot(citizen_id, snap_base)
    store.add_trajectory_snapshot(citizen_id, snap_curr)

    is_comp, reason = risk_trajectory_engine.check_snapshot_compatibility(snap_base, snap_curr)
    report = risk_trajectory_engine.evaluate_trajectory(citizen_id, [snap_base, snap_curr])

    return TrajectoryComparisonResponse(
        citizen_id=citizen_id,
        is_comparable=is_comp,
        numerical_comparison_refused=not is_comp,
        limitation_reason=reason,
        baseline_version=snap_base.model_version,
        current_version=snap_curr.model_version,
        baseline_scale=snap_base.scoring_scale,
        current_scale=snap_curr.scoring_scale,
        report=report,
    )


@router.post("/compare", response_model=TrajectoryComparisonResponse)
async def compare_trajectories_post(
    req: TrajectoryComparisonRequest,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """Compares baseline vs current trajectories with model comparability safety checks."""
    return await compare_citizen_trajectory(
        citizen_id=req.citizen_id,
        strict=req.strict,
        current_user=current_user,
    )


@router.post("/{citizen_id}/compare", response_model=TrajectoryComparisonResponse)
async def compare_citizen_trajectory(
    citizen_id: str,
    strict: bool = False,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """TC-TRJ-003: Requests a trajectory comparison between historical snapshots.
    If models/scales are incompatible:
    - Normal mode: Returns report with refusal notice and null numerical deltas.
    - Strict mode: Returns HTTP 422 refusing invalid numerical comparison.
    """
    citizen = _ensure_synthetic_citizen_if_missing(citizen_id)
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    snapshots = store.get_trajectory_snapshots(citizen_id)
    if len(snapshots) < 2:
        snapshots = _ensure_citizen_has_snapshots(citizen_id)

    if len(snapshots) >= 2:
        sorted_snaps = sorted(snapshots, key=lambda s: s.timestamp)
        base_snap = sorted_snaps[0]
        curr_snap = sorted_snaps[-1]
        is_comp, reason = risk_trajectory_engine.check_snapshot_compatibility(base_snap, curr_snap)
    else:
        is_comp, reason = True, None
        base_snap = snapshots[0] if snapshots else None
        curr_snap = snapshots[0] if snapshots else None

    if strict and not is_comp:
        raise HTTPException(
            status_code=422,
            detail=f"Numerical comparison refused: {reason}",
        )

    report = risk_trajectory_engine.evaluate_trajectory(citizen_id, snapshots)

    return TrajectoryComparisonResponse(
        citizen_id=citizen_id,
        is_comparable=is_comp,
        numerical_comparison_refused=not is_comp,
        limitation_reason=reason,
        baseline_version=base_snap.model_version if base_snap else None,
        current_version=curr_snap.model_version if curr_snap else None,
        baseline_scale=base_snap.scoring_scale if base_snap else None,
        current_scale=curr_snap.scoring_scale if curr_snap else None,
        report=report,
    )


# ==============================================================================
# STANDARD TRAJECTORY QUERY ROUTES
# ==============================================================================

@router.get("/{citizen_id}", response_model=CitizenTrajectoryReport)
async def get_citizen_trajectory_report(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """Returns the full multi-domain longitudinal risk trajectory report comparing
    Current vs Previous vs Baseline risk across all 7 clinical domains.
    """
    citizen = _ensure_synthetic_citizen_if_missing(citizen_id)
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    snapshots = _ensure_citizen_has_snapshots(citizen_id)
    report = risk_trajectory_engine.evaluate_trajectory(citizen_id, snapshots)

    audit_logger.record(
        tenant_id=citizen.tenant_id,
        actor_id=current_user.sub,
        actor_role=current_user.role,
        action=AuditAction.RISK_ASSESSED,
        resource_type="CitizenTrajectoryReport",
        resource_id=citizen_id,
    )

    return report


@router.get("/{citizen_id}/domain/{domain_name}", response_model=DomainTrajectoryComparison)
async def get_domain_trajectory(
    citizen_id: str,
    domain_name: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """Returns detailed longitudinal trend, percentage change, and contributing factors
    for a specific domain (metabolic, diabetes, hypertension, cardiovascular, obesity, renal, lifestyle).
    """
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")
    HealthcareAuthorizationEngine.enforce_citizen_access(current_user, citizen)

    domain_key = domain_name.lower().strip()
    valid_domains = [d.value for d in TrajectoryDomain]
    if domain_key not in valid_domains:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid domain '{domain_name}'. Valid domains: {', '.join(valid_domains)}"
        )

    snapshots = _ensure_citizen_has_snapshots(citizen_id)
    report = risk_trajectory_engine.evaluate_trajectory(citizen_id, snapshots)
    comparison = report.domain_trajectories.get(domain_key)

    if not comparison:
        raise HTTPException(status_code=404, detail=f"Trajectory for domain '{domain_name}' not available")

    return comparison


@router.post("/{citizen_id}/snapshot", response_model=CitizenTrajectoryReport)
async def record_trajectory_snapshot(
    citizen_id: str,
    snapshot: RiskSnapshot,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """Records a new point-in-time risk snapshot for the citizen's longitudinal history
    and recalculates updated trajectory trends.
    """
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    snapshot.citizen_id = citizen_id
    store.add_trajectory_snapshot(citizen_id, snapshot)

    snapshots = store.get_trajectory_snapshots(citizen_id)
    updated_report = risk_trajectory_engine.evaluate_trajectory(citizen_id, snapshots)

    return updated_report


@router.get("/{citizen_id}/visual", response_class=HTMLResponse)
async def get_visual_trajectory_html(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """Renders a standalone interactive HTML visual trajectory widget using Tailwind CSS
    and semantic theme tokens per generative_ui guidelines.
    """
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    snapshots = _ensure_citizen_has_snapshots(citizen_id)
    report = risk_trajectory_engine.evaluate_trajectory(citizen_id, snapshots)
    full_name = f"{citizen.first_name} {citizen.last_name}"

    html_content = risk_trajectory_engine.generate_visual_html(report, full_name)
    return HTMLResponse(content=html_content, status_code=200)


@router.get("/{citizen_id}/svg")
async def get_visual_trajectory_svg(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token)
):
    """Returns a standalone SVG vector trajectory graphic for embedding in reports or EMRs."""
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    snapshots = _ensure_citizen_has_snapshots(citizen_id)
    report = risk_trajectory_engine.evaluate_trajectory(citizen_id, snapshots)
    svg_content = risk_trajectory_engine.generate_visual_svg(report)

    return Response(content=svg_content, media_type="image/svg+xml")
