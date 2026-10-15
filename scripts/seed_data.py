import sys
import os
from pathlib import Path

# Ensure sevahealth-ai root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone, date, timedelta
import uuid

from packages.types.enums import UserRole, Gender, RiskTier, TrajectoryTrend, TriageUrgency, InterventionPillar
from packages.auth.jwt import get_password_hash
from packages.clinical_models.observations import Observation
from packages.clinical_models.screening import ScreeningSession, CBACSurvey, IDRSSurvey
from packages.clinical_models.risk import RiskAssessment, DomainRiskScores, RiskDriver, ProtectiveFactor
from packages.clinical_models.care_plan import CarePlan, DailyTask
from packages.clinical_models.triage import ClinicalTriageCase, SOAPReport
from services.store import store, UserRecord, CitizenRecord
from services.wearable.simulator import generate_synthetic_wearable_timeseries
from services.trajectory.models import RiskSnapshot


def seed_all_demo_data():
    print("Seeding SevaHealth AI demo data...")

    # 1. Seed standard role accounts and persona user credentials
    users_data = [
        ("citizen@sevahealth.ai", "citizen-user-01", UserRole.CITIZEN, "Ramesh Patel", None, None),
        ("worker@sevahealth.ai", "worker-user-01", UserRole.HEALTH_WORKER, "Sunita Devi (ASHA Worker)", "Ward 12", None),
        ("doctor@sevahealth.ai", "doctor-user-01", UserRole.CLINICIAN, "Dr. Anand Kulkarni, MD", None, ["citizen-ramesh-patel-01"]),
        ("admin@sevahealth.ai", "admin-user-01", UserRole.PUBLIC_HEALTH_ADMIN, "Dr. Radhika Rao (District Health Officer)", None, None),
        ("sysadmin@sevahealth.ai", "sysadmin-user-01", UserRole.SYSTEM_ADMIN, "System Administrator", None, None),
        ("lakshmi@sevahealth.ai", "citizen-lakshmi-id", UserRole.CITIZEN, "Lakshmi Devi", None, None),
        ("vikram@sevahealth.ai", "citizen-vikram-id", UserRole.CITIZEN, "Vikram Singh", None, None),
        ("priya@sevahealth.ai", "citizen-priya-id", UserRole.CITIZEN, "Priya Sharma", None, None),
    ]

    for email, uid, role, name, jur, pts in users_data:
        store.add_user(UserRecord(
            id=uid,
            tenant_id="karnataka_state_health",
            email=email,
            hashed_password=get_password_hash("password123"),
            role=role,
            full_name=name,
            assigned_jurisdiction=jur,
            assigned_patients=pts or [],
        ))
    print(f"  [OK] Seeded {len(users_data)} authenticated accounts (password: 'password123')")

    # 2. Seed Persona 1: Ramesh Patel (Pre-diabetic, High Risk, Deteriorating)
    p1 = CitizenRecord(
        id="citizen-ramesh-patel-01",
        tenant_id="karnataka_state_health",
        user_id="citizen-user-01",
        abha_id="91-4829-1029-4820",
        first_name="Ramesh",
        last_name="Patel",
        birth_date="1978-04-12",
        gender=Gender.MALE,
        phone="+91 98450 12345",
        state="Karnataka",
        district="Bengaluru Rural",
        sub_district="Devanahalli",
        village_or_ward="Ward 12",
    )
    store.add_citizen(p1)

    # Observations for Ramesh
    p1_obs = [
        ("SYSTOLIC_BP", 138.0, "mmHg", "8480-6", "Systolic blood pressure"),
        ("DIASTOLIC_BP", 88.0, "mmHg", "8462-4", "Diastolic blood pressure"),
        ("FASTING_GLUCOSE", 118.0, "mg/dL", "1558-6", "Fasting blood glucose"),
        ("HBA1C", 6.2, "%", "4548-4", "Hemoglobin A1c"),
        ("BMI", 27.2, "kg/m2", "39156-5", "Body mass index"),
        ("WAIST_CIRCUMFERENCE", 96.0, "cm", "8280-0", "Waist Circumference"),
        ("TRIGLYCERIDES", 185.0, "mg/dL", "2571-8", "Triglycerides"),
        ("HDL_CHOLESTEROL", 38.0, "mg/dL", "2085-9", "HDL Cholesterol"),
    ]
    for code, val, unit, loinc, name in p1_obs:
        store.add_observation(Observation(
            id=str(uuid.uuid4()),
            tenant_id=p1.tenant_id,
            citizen_id=p1.id,
            code=code,
            value=val,
            unit=unit,
            loinc_code=loinc,
            display_name=name,
            source="SCREENING_SESSION",
        ))

    # Risk Assessment for Ramesh
    p1_risk = RiskAssessment(
        id="risk-ramesh-01",
        tenant_id=p1.tenant_id,
        citizen_id=p1.id,
        overall_score=0.68,
        overall_tier=RiskTier.HIGH,
        domains=DomainRiskScores(
            diabetes_risk=0.74,
            hypertension_risk=0.62,
            cardiovascular_risk=0.45,
            metabolic_syndrome_risk=0.70,
            ckd_risk=0.25,
            fatty_liver_risk=0.55,
        ),
        trajectory=TrajectoryTrend.DETERIORATING,
        confidence_score=0.94,
        top_drivers=[
            RiskDriver(
                feature_name="Prediabetic Impaired Glycemia",
                observed_value="HbA1c 6.2%, FBG 118 mg/dL",
                target_value="HbA1c < 5.7%",
                impact_weight=0.26,
                category="BIOMETRIC",
                evidence_citation="ICMR-INDIAB Guidelines 2023"
            ),
            RiskDriver(
                feature_name="Prehypertension Vascular Strain",
                observed_value="138/88 mmHg",
                target_value="< 120/80 mmHg",
                impact_weight=0.19,
                category="BIOMETRIC",
                evidence_citation="ACC/AHA 2017 Guidelines"
            ),
            RiskDriver(
                feature_name="Central Visceral Adiposity",
                observed_value="Waist 96 cm, BMI 27.2",
                target_value="Waist < 90 cm, BMI < 23",
                impact_weight=0.15,
                category="BIOMETRIC",
                evidence_citation="Asian Indian Anthropometric Guidelines"
            ),
        ],
        protective_factors=[
            ProtectiveFactor(
                feature_name="Tobacco Abstinence",
                observed_value="Non-smoker",
                impact_weight=-0.12,
                category="LIFESTYLE"
            )
        ],
        clinical_summary="High prediabetes and metabolic progression risk. Clinical review recommended.",
    )
    store.add_risk_assessment(p1_risk)

    # 30-Day Care Plan for Ramesh
    tasks_p1 = []
    for day in range(1, 31):
        tasks_p1.append(DailyTask(
            id=f"ramesh-task-{day}",
            day=day,
            pillar=InterventionPillar.PHYSICAL_ACTIVITY if day % 2 == 0 else InterventionPillar.NUTRITION,
            title=f"Day {day}: 20-Minute Post-Dinner Brisk Walk" if day % 2 == 0 else f"Day {day}: Swap White Rice for Foxtail Millet",
            description="Proven to blunt post-prandial glycemic excursions by 28%." if day % 2 == 0 else "High-fiber whole grain stabilizes daytime insulin levels.",
            target_metric="2,500 steps" if day % 2 == 0 else "1 millet serving",
            completed=(day <= 6),  # First 6 days completed (20% adherence)
        ))
    p1_plan = CarePlan(
        id="careplan-ramesh-01",
        tenant_id=p1.tenant_id,
        citizen_id=p1.id,
        risk_assessment_id=p1_risk.id,
        title="SevaHealth 30-Day Glycemic Stabilization Journey",
        focus_domain="Prediabetes Reversal & Vascular Protection",
        start_date=date.today(),
        end_date=date.today(),
        adherence_percentage=20.0,
        nutrition_guidance="Replace 50% of refined polished grains with ragi, bajra, or foxtail millet. Increase fresh green salads.",
        activity_guidance="Target 150 minutes of moderate aerobic exercise weekly with daily post-meal brisk walking.",
        sleep_guidance="Consistent 7.5 hours sleep with screen cutoff 45 minutes before sleep.",
        stress_guidance="Practice daily 5-minute diaphragmatic breathing.",
        daily_tasks=tasks_p1,
    )
    store.set_care_plan(p1_plan)

    # Wearable simulation for Ramesh
    store.wearable_data[p1.id] = generate_synthetic_wearable_timeseries(
        citizen_id=p1.id,
        days=14,
        base_rhr=76.0,
        base_hrv=36.0,
        base_steps=5200,
        stress_trend=True,
    )

    # Seed Ramesh's Active Medications
    store.add_medication(p1.id, {
        "id": "med-ramesh-01",
        "drug_name": "Metformin",
        "dosage": "500 mg",
        "frequency": "Once daily with dinner",
        "indication": "Prediabetes glycemic control",
        "prescribed_by": "Dr. Anand Kulkarni, MD",
        "start_date": "2026-01-15",
        "is_active": True,
    })
    store.add_medication(p1.id, {
        "id": "med-ramesh-02",
        "drug_name": "Telmisartan",
        "dosage": "40 mg",
        "frequency": "Once daily morning",
        "indication": "Prehypertension cardiovascular risk reduction",
        "prescribed_by": "Dr. Anand Kulkarni, MD",
        "start_date": "2026-02-01",
        "is_active": True,
    })

    # Seed Ramesh's Recent Daily Check-In
    store.add_checkin(p1.id, {
        "id": "checkin-ramesh-01",
        "date": str(date.today()),
        "tasks_completed_count": 1,
        "tasks_total_count": 2,
        "subjective_wellbeing": "GOOD",
        "notes": "Completed post-dinner brisk walk; feeling energetic.",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })

    # Seed Ramesh's Recent Clinical Encounter
    store.add_encounter(p1.id, {
        "id": "enc-ramesh-01",
        "clinician_id": "doctor-user-01",
        "clinician_name": "Dr. Anand Kulkarni, MD",
        "encounter_type": "PREVENTIVE_HEALTH_CAMP",
        "reason": "Annual community NCD screening review",
        "soap_assessment": "Metabolic syndrome with impaired fasting glycemia and pre-hypertension.",
        "soap_plan": "Lifestyle medicine first-line: foxtail millet diet, 150m weekly activity, review in 30 days.",
        "date": "2026-09-15",
    })

    # Seed Ramesh's Lifestyle Profile
    store.set_lifestyle_profile(p1.id, {
        "patient_id": p1.id,
        "tobacco_use": "NEVER",
        "alcohol_use": "NONE",
        "dietary_pattern": "HIGH_CARB_HIGH_SALT",
        "physical_activity_level": "SEDENTARY",
        "sleep_hours_per_night": 6.5,
        "perceived_stress_level": "MODERATE",
        "recorded_at": "2026-09-15T10:00:00Z",
    })

    # Seed Ramesh's Clinical Documents
    store.add_document(p1.id, {
        "id": "doc-ramesh-01",
        "file_name": "Annual_NCD_Screening_Report_2026.pdf",
        "doc_type": "DIAGNOSTIC_LAB_REPORT",
        "file_size_bytes": 245000,
        "sha256_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "uploaded_by": "worker-user-01",
        "clinical_summary": "Comprehensive CBAC screening and point-of-care laboratory biometrics.",
        "uploaded_at": "2026-09-15T11:00:00Z",
    })

    # Seed Ramesh's Alert
    store.add_alert({
        "id": "alert-ramesh-01",
        "citizen_id": p1.id,
        "urgency": "PRIORITY",
        "alert_type": "PREDIABETES_GLYCEMIC_SPIKE",
        "message": "Impaired fasting glucose (118 mg/dL) and HbA1c (6.2%) indicative of prediabetes.",
        "clinical_rule_triggered": "ICMR_PREDIABETES_CRITERIA",
        "created_at": "2026-09-15T10:30:00Z",
        "is_acknowledged": False,
    })

    # 3. Seed Persona 2: Lakshmi Devi (Stage 2 Hypertension, Critical Escalation)
    p2 = CitizenRecord(
        id="citizen-lakshmi-devi-02",
        tenant_id="karnataka_state_health",
        user_id="citizen-lakshmi-id",
        abha_id="91-3920-5819-2049",
        first_name="Lakshmi",
        last_name="Devi",
        birth_date="1964-08-20",
        gender=Gender.FEMALE,
        phone="+91 97410 54321",
        state="Karnataka",
        district="Mandya",
        sub_district="Maddur",
        village_or_ward="Koppa Village",
    )
    store.add_citizen(p2)

    p2_obs = [
        ("SYSTOLIC_BP", 164.0, "mmHg", "8480-6", "Systolic blood pressure"),
        ("DIASTOLIC_BP", 98.0, "mmHg", "8462-4", "Diastolic blood pressure"),
        ("FASTING_GLUCOSE", 96.0, "mg/dL", "1558-6", "Fasting blood glucose"),
        ("HBA1C", 5.5, "%", "4548-4", "Hemoglobin A1c"),
        ("BMI", 24.8, "kg/m2", "39156-5", "Body mass index"),
    ]
    for code, val, unit, loinc, name in p2_obs:
        store.add_observation(Observation(
            id=str(uuid.uuid4()),
            tenant_id=p2.tenant_id,
            citizen_id=p2.id,
            code=code,
            value=val,
            unit=unit,
            loinc_code=loinc,
            display_name=name,
            source="COMMUNITY_HEALTH_CAMP",
        ))

    p2_risk = RiskAssessment(
        id="risk-lakshmi-02",
        tenant_id=p2.tenant_id,
        citizen_id=p2.id,
        overall_score=0.82,
        overall_tier=RiskTier.CRITICAL,
        domains=DomainRiskScores(
            diabetes_risk=0.15,
            hypertension_risk=0.94,
            cardiovascular_risk=0.88,
            metabolic_syndrome_risk=0.40,
            ckd_risk=0.35,
            fatty_liver_risk=0.25,
        ),
        trajectory=TrajectoryTrend.DETERIORATING,
        confidence_score=0.90,
        top_drivers=[
            RiskDriver(
                feature_name="Stage 2 Severe Hypertension",
                observed_value="164/98 mmHg",
                target_value="< 130/80 mmHg",
                impact_weight=0.34,
                category="BIOMETRIC",
                evidence_citation="ACC/AHA 2017 / WHO Essential Interventions"
            ),
            RiskDriver(
                feature_name="Cardiovascular Ischemic Danger",
                observed_value="High 10-Yr CVD Matrix",
                target_value="Low < 10%",
                impact_weight=0.28,
                category="BIOMETRIC",
                evidence_citation="WHO-SEAR CVD Risk Charts"
            )
        ],
        clinical_summary="CRITICAL HYPERTENSIVE STRAIN: Immediate medical officer triage required.",
    )
    store.add_risk_assessment(p2_risk)

    # Enqueue Lakshmi into Clinical Triage Queue
    p2_triage = ClinicalTriageCase(
        id="triage-lakshmi-01",
        tenant_id=p2.tenant_id,
        citizen_id=p2.id,
        citizen_name="Lakshmi Devi",
        risk_assessment_id=p2_risk.id,
        urgency=TriageUrgency.EMERGENT,
        escalation_reason="Severe Stage 2 Hypertension (164/98 mmHg) detected during ASHA field screening.",
        soap_note=SOAPReport(
            subjective="Elderly female (age 62) screened at Koppa PHC camp. Reports occasional morning headaches and dizziness.",
            objective="BP 164/98 mmHg confirmed on duplicate measurement. Normal glucose (FBG 96 mg/dL, HbA1c 5.5%).",
            assessment="Severe Stage 2 Essential Hypertension with high 10-year cardiovascular risk.",
            plan="1. Immediate Medical Officer evaluation. 2. Low-sodium dietary calibration (< 2g/day). 3. Rule out end-organ involvement.",
        ),
    )
    store.add_triage_case(p2_triage)

    # 4. Seed Persona 3: Vikram Singh (High-Stress Transport Driver)
    p3 = CitizenRecord(
        id="citizen-vikram-singh-03",
        tenant_id="karnataka_state_health",
        user_id="citizen-vikram-id",
        abha_id="91-5829-3910-1920",
        first_name="Vikram",
        last_name="Singh",
        birth_date="1987-11-05",
        gender=Gender.MALE,
        phone="+91 99887 76655",
        state="Karnataka",
        district="Hassan",
        sub_district="Channarayapatna",
        village_or_ward="Highway Corridor Ward",
    )
    store.add_citizen(p3)

    # 5. Seed Persona 4: Priya Sharma (Active Baseline, Low Risk)
    p4 = CitizenRecord(
        id="citizen-priya-sharma-04",
        tenant_id="karnataka_state_health",
        user_id="citizen-priya-id",
        abha_id="91-1029-3849-5910",
        first_name="Priya",
        last_name="Sharma",
        birth_date="1997-02-14",
        gender=Gender.FEMALE,
        phone="+91 94480 99887",
        state="Karnataka",
        district="Mysuru",
        sub_district="Mysuru Urban",
        village_or_ward="Gokulam Ward 3",
    )
    store.add_citizen(p4)

    store.add_risk_assessment(RiskAssessment(
        id="risk-priya-04",
        tenant_id=p4.tenant_id,
        citizen_id=p4.id,
        overall_score=0.14,
        overall_tier=RiskTier.LOW,
        domains=DomainRiskScores(
            diabetes_risk=0.10,
            hypertension_risk=0.10,
            cardiovascular_risk=0.08,
            metabolic_syndrome_risk=0.12,
            ckd_risk=0.05,
            fatty_liver_risk=0.08,
        ),
        trajectory=TrajectoryTrend.IMPROVING,
        confidence_score=0.96,
        clinical_summary="Optimal metabolic and cardiovascular profile. Sustain annual preventive screening.",
    ))

    # --- SEED LONGITUDINAL RISK TRAJECTORIES ---
    now = datetime.now(timezone.utc)

    # 1. Ramesh Patel: Baseline (180d ago) -> Previous (90d ago) -> Current (Today) - WORSENING METABOLIC
    ramesh_snapshots = [
        RiskSnapshot(
            snapshot_id="snap-ramesh-base",
            citizen_id=p1.id,
            timestamp=now - timedelta(days=180),
            domain_scores={
                "metabolic": 0.35,
                "diabetes": 0.38,
                "hypertension": 0.30,
                "cardiovascular": 0.28,
                "obesity": 0.36,
                "renal": 0.12,
                "lifestyle": 0.28,
            },
            domain_tiers={
                "metabolic": "MODERATE",
                "diabetes": "MODERATE",
                "hypertension": "MODERATE",
                "cardiovascular": "MODERATE",
                "obesity": "MODERATE",
                "renal": "LOW",
                "lifestyle": "LOW",
            },
            key_biomarkers={
                "HBA1C": 5.7,
                "FASTING_GLUCOSE": 102.0,
                "SYSTOLIC_BP": 126.0,
                "DIASTOLIC_BP": 80.0,
                "WAIST_CIRCUMFERENCE": 91.0,
                "BMI": 25.5,
                "TOTAL_CHOLESTEROL": 185.0,
                "TRIGLYCERIDES": 150.0,
                "EGFR": 92.0,
                "STEPS": 8200,
                "HRV": 52.0,
            },
            source="COMMUNITY_SCREENING",
        ),
        RiskSnapshot(
            snapshot_id="snap-ramesh-prev",
            citizen_id=p1.id,
            timestamp=now - timedelta(days=90),
            domain_scores={
                "metabolic": 0.50,
                "diabetes": 0.52,
                "hypertension": 0.46,
                "cardiovascular": 0.36,
                "obesity": 0.52,
                "renal": 0.16,
                "lifestyle": 0.44,
            },
            domain_tiers={
                "metabolic": "MODERATE",
                "diabetes": "MODERATE",
                "hypertension": "MODERATE",
                "cardiovascular": "MODERATE",
                "obesity": "MODERATE",
                "renal": "LOW",
                "lifestyle": "MODERATE",
            },
            key_biomarkers={
                "HBA1C": 6.0,
                "FASTING_GLUCOSE": 110.0,
                "SYSTOLIC_BP": 132.0,
                "DIASTOLIC_BP": 84.0,
                "WAIST_CIRCUMFERENCE": 94.0,
                "BMI": 26.4,
                "TOTAL_CHOLESTEROL": 195.0,
                "TRIGLYCERIDES": 170.0,
                "EGFR": 88.0,
                "STEPS": 6400,
                "HRV": 46.0,
            },
            source="PHC_FOLLOWUP",
        ),
        RiskSnapshot(
            snapshot_id="snap-ramesh-curr",
            citizen_id=p1.id,
            timestamp=now,
            domain_scores={
                "metabolic": 0.65,
                "diabetes": 0.68,
                "hypertension": 0.62,
                "cardiovascular": 0.44,
                "obesity": 0.64,
                "renal": 0.22,
                "lifestyle": 0.58,
            },
            domain_tiers={
                "metabolic": "HIGH",
                "diabetes": "HIGH",
                "hypertension": "HIGH",
                "cardiovascular": "MODERATE",
                "obesity": "HIGH",
                "renal": "MODERATE",
                "lifestyle": "HIGH",
            },
            key_biomarkers={
                "HBA1C": 6.2,
                "FASTING_GLUCOSE": 118.0,
                "SYSTOLIC_BP": 138.0,
                "DIASTOLIC_BP": 88.0,
                "WAIST_CIRCUMFERENCE": 96.0,
                "BMI": 27.2,
                "TOTAL_CHOLESTEROL": 205.0,
                "TRIGLYCERIDES": 185.0,
                "EGFR": 85.0,
                "STEPS": 5200,
                "HRV": 40.0,
            },
            source="CURRENT_CAMP",
        ),
    ]
    store.set_trajectory_snapshots(p1.id, ramesh_snapshots)

    # 2. Lakshmi Devi: Severe Stage 2 HTN Evolution
    lakshmi_snapshots = [
        RiskSnapshot(
            snapshot_id="snap-lakshmi-base",
            citizen_id=p2.id,
            timestamp=now - timedelta(days=90),
            domain_scores={
                "metabolic": 0.30,
                "diabetes": 0.20,
                "hypertension": 0.72,
                "cardiovascular": 0.65,
                "obesity": 0.28,
                "renal": 0.35,
                "lifestyle": 0.35,
            },
            domain_tiers={
                "metabolic": "MODERATE",
                "diabetes": "LOW",
                "hypertension": "HIGH",
                "cardiovascular": "HIGH",
                "obesity": "MODERATE",
                "renal": "MODERATE",
                "lifestyle": "MODERATE",
            },
            key_biomarkers={
                "SYSTOLIC_BP": 150.0,
                "DIASTOLIC_BP": 92.0,
                "FASTING_GLUCOSE": 94.0,
                "HBA1C": 5.4,
                "BMI": 23.5,
                "WAIST_CIRCUMFERENCE": 82.0,
            },
            source="FIELD_CAMP",
        ),
        RiskSnapshot(
            snapshot_id="snap-lakshmi-curr",
            citizen_id=p2.id,
            timestamp=now,
            domain_scores={
                "metabolic": 0.35,
                "diabetes": 0.22,
                "hypertension": 0.88,
                "cardiovascular": 0.78,
                "obesity": 0.30,
                "renal": 0.42,
                "lifestyle": 0.40,
            },
            domain_tiers={
                "metabolic": "MODERATE",
                "diabetes": "LOW",
                "hypertension": "CRITICAL",
                "cardiovascular": "HIGH",
                "obesity": "MODERATE",
                "renal": "MODERATE",
                "lifestyle": "MODERATE",
            },
            key_biomarkers={
                "SYSTOLIC_BP": 164.0,
                "DIASTOLIC_BP": 98.0,
                "FASTING_GLUCOSE": 96.0,
                "HBA1C": 5.5,
                "BMI": 23.8,
                "WAIST_CIRCUMFERENCE": 83.0,
            },
            source="ASHA_CAMP_ESCALATION",
        ),
    ]
    store.set_trajectory_snapshots(p2.id, lakshmi_snapshots)

    # 3. Priya Sharma: Stable Low-Risk Trajectory
    priya_snapshots = [
        RiskSnapshot(
            snapshot_id="snap-priya-base",
            citizen_id=p4.id,
            timestamp=now - timedelta(days=180),
            domain_scores={
                "metabolic": 0.12,
                "diabetes": 0.10,
                "hypertension": 0.10,
                "cardiovascular": 0.08,
                "obesity": 0.12,
                "renal": 0.05,
                "lifestyle": 0.08,
            },
            domain_tiers={
                "metabolic": "LOW",
                "diabetes": "LOW",
                "hypertension": "LOW",
                "cardiovascular": "LOW",
                "obesity": "LOW",
                "renal": "LOW",
                "lifestyle": "LOW",
            },
            key_biomarkers={
                "SYSTOLIC_BP": 112.0,
                "DIASTOLIC_BP": 72.0,
                "FASTING_GLUCOSE": 88.0,
                "HBA1C": 5.1,
                "BMI": 21.0,
                "WAIST_CIRCUMFERENCE": 74.0,
                "STEPS": 9500,
            },
            source="ANNUAL_HEALTH_CAMP",
        ),
        RiskSnapshot(
            snapshot_id="snap-priya-curr",
            citizen_id=p4.id,
            timestamp=now,
            domain_scores={
                "metabolic": 0.10,
                "diabetes": 0.08,
                "hypertension": 0.09,
                "cardiovascular": 0.07,
                "obesity": 0.10,
                "renal": 0.05,
                "lifestyle": 0.06,
            },
            domain_tiers={
                "metabolic": "LOW",
                "diabetes": "LOW",
                "hypertension": "LOW",
                "cardiovascular": "LOW",
                "obesity": "LOW",
                "renal": "LOW",
                "lifestyle": "LOW",
            },
            key_biomarkers={
                "SYSTOLIC_BP": 110.0,
                "DIASTOLIC_BP": 70.0,
                "FASTING_GLUCOSE": 86.0,
                "HBA1C": 5.0,
                "BMI": 20.8,
                "WAIST_CIRCUMFERENCE": 73.0,
                "STEPS": 10200,
            },
            source="FOLLOWUP_CHECKUP",
        ),
    ]
    store.set_trajectory_snapshots(p4.id, priya_snapshots)

    # Seed complete 12 synthetic demo personas
    from services.demo.personas import seed_all_synthetic_personas
    seed_all_synthetic_personas()

    print("  [OK] Seeded 12 comprehensive synthetic personas across all clinical NCD phenotypes")
    print("  [OK] Enqueued Lakshmi Devi into Clinician Triage Queue (EMERGENT)")
    print("  [OK] Populated 30-day care plan and 14-day wearable telemetry for Ramesh Patel")
    print("Seeding completed successfully!")


if __name__ == "__main__":
    if "--reset" in sys.argv:
        print("[RESET] Triggering demo environment reset before seeding clean baseline...")
        from services.demo.challenge_workflow import reset_challenge_demo
        res = reset_challenge_demo()
        print(f"  [OK] Reset status: {res.get('status')}")
    seed_all_demo_data()
