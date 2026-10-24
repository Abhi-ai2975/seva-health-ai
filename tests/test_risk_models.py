import pytest
from datetime import datetime, timezone

from services.risk_engine.base import (
    RiskCategory,
    ImpactDirection,
    PhysiologicalValidationError,
    UnitNormalizer,
    PhysiologicalRangeValidator,
    DomainRiskResult,
)
from services.risk_engine.models.diabetes import DiabetesRiskModel
from services.risk_engine.models.hypertension import HypertensionRiskModel
from services.risk_engine.models.cardiovascular import CardiovascularRiskModel
from services.risk_engine.models.obesity import ObesityMetabolicRiskModel
from services.risk_engine.models.ckd import CKDRiskModel
from services.risk_engine.engine import NCDRiskEngine
from services.wearable.models import WearableProjection
from packages.types.enums import TrajectoryTrend


# =========================================================================
# 1. UNIT NORMALIZER & PHYSIOLOGICAL VALIDATION TESTS
# =========================================================================

def test_unit_normalizer_conversions():
    # Glucose: mmol/L to mg/dL
    val, unit = UnitNormalizer.normalize("FASTING_GLUCOSE", 6.0, "mmol/L")
    assert unit == "mg/dL"
    assert round(val, 1) == 108.1

    # Cholesterol: mmol/L to mg/dL
    val, unit = UnitNormalizer.normalize("TOTAL_CHOLESTEROL", 5.2, "mmol/L")
    assert unit == "mg/dL"
    assert round(val, 1) == 201.1

    # Creatinine: umol/L to mg/dL
    val, unit = UnitNormalizer.normalize("SERUM_CREATININE", 120.0, "umol/L")
    assert unit == "mg/dL"
    assert round(val, 2) == 1.36

    # Height: inches to cm
    val, unit = UnitNormalizer.normalize("HEIGHT", 70.0, "inches")
    assert unit == "cm"
    assert round(val, 1) == 177.8

    # Weight: lbs to kg
    val, unit = UnitNormalizer.normalize("WEIGHT", 176.0, "lbs")
    assert unit == "kg"
    assert round(val, 1) == 79.8

    # Blood Pressure: kPa to mmHg
    val, unit = UnitNormalizer.normalize("SYSTOLIC_BP", 16.0, "kPa")
    assert unit == "mmHg"
    assert round(val, 1) == 120.0


def test_physiological_range_validator_boundary_checks():
    # Plausible values must pass
    PhysiologicalRangeValidator.validate("SYSTOLIC_BP", 120.0, "mmHg")
    PhysiologicalRangeValidator.validate("FASTING_GLUCOSE", 95.0, "mg/dL")
    PhysiologicalRangeValidator.validate("HBA1C", 6.5, "%")

    # Implausible values must raise PhysiologicalValidationError
    with pytest.raises(PhysiologicalValidationError) as exc_info:
        PhysiologicalRangeValidator.validate("SYSTOLIC_BP", 350.0, "mmHg")
    assert "Expected physiological range is [60.0, 280.0] mmHg" in str(exc_info.value)

    with pytest.raises(PhysiologicalValidationError):
        PhysiologicalRangeValidator.validate("FASTING_GLUCOSE", 15.0, "mg/dL")

    with pytest.raises(PhysiologicalValidationError):
        PhysiologicalRangeValidator.validate("HBA1C", 25.0, "%")

    with pytest.raises(PhysiologicalValidationError):
        PhysiologicalRangeValidator.validate("HEIGHT", 320.0, "cm")


# =========================================================================
# 2. DOMAIN MODEL UNIT TESTS & GOLDEN CASES
# =========================================================================

def test_diabetes_model_golden_cases():
    model = DiabetesRiskModel()
    assert model.is_clinically_validated() is True
    assert "ICMR" in model.provenance()["authority"]

    # Golden Case 1: Diabetic Patient (HbA1c = 7.8%)
    diabetic_inputs = {"AGE": 52, "HBA1C": 7.8, "FASTING_GLUCOSE": 145.0}
    res_diab = model.calculate(diabetic_inputs)
    assert res_diab.risk_category == RiskCategory.HIGH
    assert res_diab.score >= 0.70
    assert any("Diabetic" in c.feature for c in res_diab.risk_factor_contributions)
    assert res_diab.is_clinically_validated is True

    # Golden Case 2: Prediabetic Patient (HbA1c = 5.9%, FBG = 110)
    prediab_inputs = {"AGE": 44, "HBA1C": 5.9, "FASTING_GLUCOSE": 110.0}
    res_prediab = model.calculate(prediab_inputs)
    assert res_prediab.risk_category == RiskCategory.MODERATE
    assert 0.40 <= res_prediab.score <= 0.70

    # Golden Case 3: Normoglycemic Patient (HbA1c = 5.1%)
    normal_inputs = {"AGE": 28, "HBA1C": 5.1, "FASTING_GLUCOSE": 88.0}
    res_normal = model.calculate(normal_inputs)
    assert res_normal.risk_category == RiskCategory.LOW
    assert res_normal.score <= 0.25
    assert any(c.impact_direction == ImpactDirection.DECREASES_RISK for c in res_normal.risk_factor_contributions)


def test_hypertension_model_golden_cases():
    model = HypertensionRiskModel()
    assert model.is_clinically_validated() is True

    # Golden Case 1: Stage 2 Severe HTN
    severe_inputs = {"SYSTOLIC_BP": 165.0, "DIASTOLIC_BP": 105.0}
    res_severe = model.calculate(severe_inputs)
    assert res_severe.risk_category == RiskCategory.HIGH
    assert res_severe.score >= 0.85
    assert "Stage 2" in res_severe.risk_factor_contributions[0].feature

    # Golden Case 2: Stage 1 HTN
    stage1_inputs = {"SYSTOLIC_BP": 134.0, "DIASTOLIC_BP": 84.0}
    res_stage1 = model.calculate(stage1_inputs)
    assert res_stage1.risk_category == RiskCategory.MODERATE

    # Golden Case 3: Optimal BP
    optimal_inputs = {"SYSTOLIC_BP": 115.0, "DIASTOLIC_BP": 75.0}
    res_opt = model.calculate(optimal_inputs)
    assert res_opt.risk_category == RiskCategory.LOW
    assert any("Optimal" in c.feature for c in res_opt.risk_factor_contributions)


def test_cardiovascular_model_golden_cases():
    model = CardiovascularRiskModel()
    assert model.is_clinically_validated() is True
    assert "WHO" in model.provenance()["authority"]

    # Golden Case 1: High ASCVD Risk (Older, smoker, elevated BP & cholesterol)
    high_cvd_inputs = {
        "AGE": 68,
        "SYSTOLIC_BP": 158.0,
        "TOTAL_CHOLESTEROL": 250.0,
        "SMOKING": True,
        "SEX": "MALE",
    }
    res_high = model.calculate(high_cvd_inputs)
    assert res_high.risk_category == RiskCategory.HIGH
    assert res_high.score >= 0.50
    assert any("Tobacco" in c.feature for c in res_high.risk_factor_contributions)

    # Golden Case 2: Non-laboratory BMI adaptation fallback
    non_lab_inputs = {
        "AGE": 55,
        "SYSTOLIC_BP": 135.0,
        "BMI": 28.5,
        "SMOKING": False,
        # TOTAL_CHOLESTEROL is deliberately omitted
    }
    res_non_lab = model.calculate(non_lab_inputs)
    assert res_non_lab.risk_category in [RiskCategory.LOW, RiskCategory.MODERATE]
    assert any("BMI proxy" in lim for lim in res_non_lab.limitations)


def test_obesity_metabolic_model_south_asian_cutoffs():
    model = ObesityMetabolicRiskModel()
    assert model.is_clinically_validated() is True

    # Asian Indian Male with central obesity (Waist >= 90 cm, BMI >= 25)
    asian_indian_male = {
        "WAIST_CIRCUMFERENCE": 96.0,
        "BMI": 26.8,
        "SEX": "MALE",
        "TRIGLYCERIDES": 185.0,
        "HDL_CHOLESTEROL": 38.0,
    }
    res = model.calculate(asian_indian_male)
    assert res.risk_category == RiskCategory.HIGH
    assert res.score >= 0.60
    assert any("Visceral Adiposity" in c.feature or "Central Obesity" in c.feature for c in res.risk_factor_contributions)
    assert any("Hypertriglyceridemia" in c.feature or "Obesity" in c.feature for c in res.risk_factor_contributions)

    # Healthy profile
    healthy_inputs = {
        "WAIST_CIRCUMFERENCE": 78.0,
        "BMI": 21.5,
        "SEX": "MALE",
    }
    res_healthy = model.calculate(healthy_inputs)
    assert res_healthy.risk_category == RiskCategory.LOW
    assert res_healthy.score <= 0.20


def test_ckd_model_and_demonstration_marking():
    model = CKDRiskModel()

    # Validated case: G3b CKD
    ckd_inputs = {"AGE": 62, "SEX": "FEMALE", "EGFR": 38.0}
    res_validated = model.calculate(ckd_inputs)
    assert res_validated.risk_category == RiskCategory.HIGH
    assert res_validated.is_clinically_validated is True
    assert "KDIGO" in res_validated.provenance["guideline"]

    # Demo case: No creatinine or eGFR provided -> Heuristic proxy marked DEMONSTRATION ONLY
    demo_inputs = {
        "AGE": 60,
        "SEX": "MALE",
        "SYSTOLIC_BP": 155.0,
        "HBA1C": 8.0,
        # SERUM_CREATININE and EGFR absent
    }
    res_demo = model.calculate(demo_inputs)
    assert res_demo.is_clinically_validated is False
    assert any("DEMONSTRATION ONLY" in lim for lim in res_demo.limitations)
    assert "DEMONSTRATION" in res_demo.provenance["validation_status"]


# =========================================================================
# 3. MISSING DATA & ZERO-SILENT-SUBSTITUTION TESTS
# =========================================================================

def test_missing_data_triggers_insufficient_data():
    # Hypertension model requires SYSTOLIC_BP or DIASTOLIC_BP
    htn_model = HypertensionRiskModel()
    empty_inputs = {"AGE": 40}  # No blood pressure measurements
    res = htn_model.calculate(empty_inputs)

    assert res.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert res.score is None
    assert res.confidence == 0.0
    assert any("Missing mandatory" in lim for lim in res.limitations)

    # Cardiovascular model requires AGE and SYSTOLIC_BP
    cvd_model = CardiovascularRiskModel()
    res_cvd = cvd_model.calculate({"AGE": 45})  # SBP missing
    assert res_cvd.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert res_cvd.score is None
    assert res_cvd.confidence == 0.0

    # Obesity model requires BMI or (HEIGHT + WEIGHT) or WAIST_CIRCUMFERENCE
    obesity_model = ObesityMetabolicRiskModel()
    res_obesity = obesity_model.calculate({"AGE": 45})
    assert res_obesity.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert res_obesity.score is None


# =========================================================================
# 4. NCD RISK ENGINE ORCHESTRATION & WEARABLE MODULATION TESTS
# =========================================================================

def test_ncd_risk_engine_orchestration():
    engine = NCDRiskEngine()
    models = engine.list_models()
    assert len(models) >= 5
    domains = [m["key"] for m in models]
    assert {"diabetes", "hypertension", "cardiovascular", "obesity", "ckd"}.issubset(set(domains))

    # Evaluate full metabolic syndrome profile
    raw_inputs = {
        "AGE": 56,
        "SEX": "MALE",
        "SYSTOLIC_BP": 150.0,
        "DIASTOLIC_BP": 95.0,
        "HBA1C": 7.4,
        "FASTING_GLUCOSE": 138.0,
        "TOTAL_CHOLESTEROL": 240.0,
        "HDL_CHOLESTEROL": 36.0,
        "TRIGLYCERIDES": 210.0,
        "BMI": 28.2,
        "WAIST_CIRCUMFERENCE": 98.0,
        "SMOKING": True,
        "EGFR": 72.0,
    }

    assessment = engine.evaluate(raw_inputs)
    assert assessment.overall_category == RiskCategory.HIGH
    assert assessment.overall_score >= 0.55
    assert len(assessment.top_drivers) >= 3
    assert assessment.top_drivers[0].impact_weight >= assessment.top_drivers[1].impact_weight
    assert len(assessment.recommended_next_steps) >= 3
    assert "SAFETY NOTICE" in assessment.clinical_safety_notice


def test_wearable_modulation_on_trajectory():
    engine = NCDRiskEngine()
    raw_inputs = {
        "AGE": 50,
        "SEX": "MALE",
        "SYSTOLIC_BP": 142.0,
        "DIASTOLIC_BP": 90.0,
        "HBA1C": 6.8,
        "BMI": 26.0,
        "WAIST_CIRCUMFERENCE": 92.0,
    }

    # Without wearable: HIGH risk -> DETERIORATING
    base_res = engine.evaluate(raw_inputs)
    assert base_res.trajectory == TrajectoryTrend.DETERIORATING

    # With high adherence wearable (10,000 steps, high adherence) -> modulates towards STABLE
    healthy_wearable = WearableProjection(
        citizen_id="c1",
        avg_resting_heart_rate=64.0,
        rhr_trend_delta=-2.0,
        avg_hrv_rmssd=48.0,
        hrv_suppression_flag=False,
        avg_daily_steps=10500,
        step_target_adherence_pct=0.92,
        avg_active_minutes_daily=45.0,
        avg_sleep_duration_hours=7.5,
        chronic_sleep_deficit=False,
        avg_deep_sleep_pct=22.0,
        avg_active_calories=450.0,
    )
    wearable_res = engine.evaluate(raw_inputs, wearable_projection=healthy_wearable)
    assert wearable_res.trajectory == TrajectoryTrend.STABLE
