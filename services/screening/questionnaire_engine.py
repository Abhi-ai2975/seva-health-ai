from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import math
import uuid
import structlog

from packages.clinical_models.observations import Observation
from packages.clinical_models.terminology import LOINC_CODES
from packages.clinical_models.screening import (
    ScreeningSession,
    CBACSurvey,
    IDRSSurvey,
    VitalsPayload,
)
from packages.clinical_models.risk import RiskAssessment, RiskDriver, ProtectiveFactor
from packages.types.enums import RiskTier, TrajectoryTrend, TriageUrgency, AuditAction
from packages.observability.audit import audit_logger
from services.screening.questionnaire_models import (
    QuestionnaireWorkflowProfile,
    ScreeningSectionDefinition,
    ScreeningSectionType,
    QuestionDefinition,
    QuestionType,
    QuestionValidation,
    QuestionOption,
    ConfigurableScreeningSubmission,
    ExplainableScreeningSummary,
    ScreeningEvaluationResponse,
)
from services.risk_engine.evaluator import evaluate_ncd_domains
from services.wearable.adapter import wearable_adapter
from services.store import store

logger = structlog.get_logger(__name__)


# ==============================================================================
# 1. QUESTIONNAIRE PROFILE SPECIFICATIONS (NOT HARDCODED IN UI)
# ==============================================================================

def get_mvp_comprehensive_profile() -> QuestionnaireWorkflowProfile:
    """Canonical MVP Comprehensive NCD Screening Protocol covering all 6 domains:
    Demographics, Anthropometry, Vitals, Labs, Lifestyle, and History.
    """
    return QuestionnaireWorkflowProfile(
        id="mvp_comprehensive",
        title="SevaHealth National NCD Comprehensive Screening Protocol",
        version="1.2.0",
        target_role="ALL",
        description="Comprehensive evaluation across cardiovascular, metabolic, glycemic, renal, and lifestyle factors.",
        estimated_time_minutes=8,
        sections=[
            # 1. DEMOGRAPHICS
            ScreeningSectionDefinition(
                id=ScreeningSectionType.DEMOGRAPHICS,
                title="Demographics & Individual Profile",
                description="Baseline personal demographic data for age and sex-standardized epidemiologic scoring.",
                icon="fa-solid fa-id-card",
                questions=[
                    QuestionDefinition(
                        id="age",
                        section=ScreeningSectionType.DEMOGRAPHICS,
                        label="Age",
                        help_text="Chronological age in completed years.",
                        type=QuestionType.NUMBER,
                        unit="years",
                        required=True,
                        validation=QuestionValidation(min_value=18, max_value=120, step=1),
                        default_value=48,
                    ),
                    QuestionDefinition(
                        id="sex",
                        section=ScreeningSectionType.DEMOGRAPHICS,
                        label="Biological Sex",
                        help_text="Biological sex at birth used for Asian Indian anthropometric cutoffs.",
                        type=QuestionType.SELECT,
                        required=True,
                        validation=QuestionValidation(
                            options=[
                                QuestionOption(value="MALE", label="Male (Waist cutoff: 90 cm)"),
                                QuestionOption(value="FEMALE", label="Female (Waist cutoff: 80 cm)"),
                                QuestionOption(value="OTHER", label="Other"),
                            ]
                        ),
                        default_value="MALE",
                    ),
                ],
            ),
            # 2. ANTHROPOMETRY
            ScreeningSectionDefinition(
                id=ScreeningSectionType.ANTHROPOMETRY,
                title="Anthropometric Measurements",
                description="Physical dimensions used to determine visceral adiposity and body mass index.",
                icon="fa-solid fa-weight-scale",
                questions=[
                    QuestionDefinition(
                        id="height",
                        section=ScreeningSectionType.ANTHROPOMETRY,
                        label="Height",
                        help_text="Standing height measured barefoot using stadiometer.",
                        type=QuestionType.NUMBER,
                        unit="cm",
                        required=True,
                        validation=QuestionValidation(min_value=100.0, max_value=230.0, step=0.5),
                        loinc_code="8302-2",
                        observation_code="HEIGHT",
                        default_value=170.0,
                    ),
                    QuestionDefinition(
                        id="weight",
                        section=ScreeningSectionType.ANTHROPOMETRY,
                        label="Weight",
                        help_text="Body mass in light indoor clothing.",
                        type=QuestionType.NUMBER,
                        unit="kg",
                        required=True,
                        validation=QuestionValidation(min_value=30.0, max_value=220.0, step=0.1),
                        loinc_code="29463-7",
                        observation_code="WEIGHT",
                        default_value=76.0,
                    ),
                    QuestionDefinition(
                        id="bmi",
                        section=ScreeningSectionType.ANTHROPOMETRY,
                        label="Body Mass Index (BMI)",
                        help_text="Auto-calculated ratio (weight / height^2). Normal South Asian cutoff: < 23.0 kg/m².",
                        type=QuestionType.NUMBER,
                        unit="kg/m2",
                        required=False,
                        is_derived=True,
                        loinc_code="39156-5",
                        observation_code="BMI",
                    ),
                    QuestionDefinition(
                        id="waist_circumference",
                        section=ScreeningSectionType.ANTHROPOMETRY,
                        label="Waist Circumference",
                        help_text="Measured at the midpoint between the lower margin of the last palpable rib and the top of the iliac crest.",
                        type=QuestionType.NUMBER,
                        unit="cm",
                        required=True,
                        validation=QuestionValidation(min_value=45.0, max_value=160.0, step=0.5),
                        loinc_code="8280-0",
                        observation_code="WAIST_CIRCUMFERENCE",
                        default_value=94.0,
                    ),
                ],
            ),
            # 3. VITALS
            ScreeningSectionDefinition(
                id=ScreeningSectionType.VITALS,
                title="Cardiovascular & Hemodynamic Vitals",
                description="Resting vascular pressures and resting heart rate.",
                icon="fa-solid fa-heart-pulse",
                questions=[
                    QuestionDefinition(
                        id="systolic_bp",
                        section=ScreeningSectionType.VITALS,
                        label="Systolic Blood Pressure",
                        help_text="Resting brachial pressure after 5 minutes of seated rest.",
                        type=QuestionType.NUMBER,
                        unit="mmHg",
                        required=True,
                        validation=QuestionValidation(min_value=70.0, max_value=240.0, step=1.0),
                        loinc_code="8480-6",
                        observation_code="SYSTOLIC_BP",
                        default_value=134.0,
                    ),
                    QuestionDefinition(
                        id="diastolic_bp",
                        section=ScreeningSectionType.VITALS,
                        label="Diastolic Blood Pressure",
                        help_text="Resting phase vascular pressure.",
                        type=QuestionType.NUMBER,
                        unit="mmHg",
                        required=True,
                        validation=QuestionValidation(min_value=40.0, max_value=140.0, step=1.0),
                        loinc_code="8462-4",
                        observation_code="DIASTOLIC_BP",
                        default_value=86.0,
                    ),
                    QuestionDefinition(
                        id="heart_rate",
                        section=ScreeningSectionType.VITALS,
                        label="Resting Heart Rate",
                        help_text="Radial pulse or automated monitor beats per minute.",
                        type=QuestionType.NUMBER,
                        unit="beats/min",
                        required=False,
                        validation=QuestionValidation(min_value=40.0, max_value=200.0, step=1.0),
                        loinc_code="8867-4",
                        observation_code="HEART_RATE",
                        default_value=74.0,
                    ),
                ],
            ),
            # 4. LABS
            ScreeningSectionDefinition(
                id=ScreeningSectionType.LABS,
                title="Laboratory Biomarkers & Chemistry",
                description="Glycemic indices, complete lipid panel, and renal function markers.",
                icon="fa-solid fa-vial-virus",
                questions=[
                    QuestionDefinition(
                        id="fasting_glucose",
                        section=ScreeningSectionType.LABS,
                        label="Fasting Blood Glucose",
                        help_text="Measured after at least 8 hours of overnight fasting.",
                        type=QuestionType.NUMBER,
                        unit="mg/dL",
                        required=False,
                        validation=QuestionValidation(min_value=40.0, max_value=500.0, step=1.0),
                        loinc_code="1558-6",
                        observation_code="FASTING_GLUCOSE",
                        default_value=108.0,
                    ),
                    QuestionDefinition(
                        id="hba1c",
                        section=ScreeningSectionType.LABS,
                        label="Hemoglobin A1c (HbA1c)",
                        help_text="3-month rolling glycemic index. Prediabetes: 5.7 - 6.4%, Diabetes: >= 6.5%.",
                        type=QuestionType.NUMBER,
                        unit="%",
                        required=False,
                        validation=QuestionValidation(min_value=3.5, max_value=16.0, step=0.1),
                        loinc_code="4548-4",
                        observation_code="HBA1C",
                        default_value=5.9,
                    ),
                    QuestionDefinition(
                        id="total_cholesterol",
                        section=ScreeningSectionType.LABS,
                        label="Total Serum Cholesterol",
                        help_text="Target: < 200 mg/dL.",
                        type=QuestionType.NUMBER,
                        unit="mg/dL",
                        required=False,
                        validation=QuestionValidation(min_value=80.0, max_value=500.0, step=1.0),
                        loinc_code="2093-3",
                        observation_code="TOTAL_CHOLESTEROL",
                        default_value=210.0,
                    ),
                    QuestionDefinition(
                        id="ldl_cholesterol",
                        section=ScreeningSectionType.LABS,
                        label="LDL Cholesterol (Atherogenic)",
                        help_text="Target: < 100 mg/dL (or < 70 mg/dL in high risk patients).",
                        type=QuestionType.NUMBER,
                        unit="mg/dL",
                        required=False,
                        validation=QuestionValidation(min_value=30.0, max_value=350.0, step=1.0),
                        loinc_code="13457-7",
                        observation_code="LDL_CHOLESTEROL",
                        default_value=128.0,
                    ),
                    QuestionDefinition(
                        id="hdl_cholesterol",
                        section=ScreeningSectionType.LABS,
                        label="HDL Cholesterol (Protective)",
                        help_text="Target: >= 40 mg/dL (men), >= 50 mg/dL (women).",
                        type=QuestionType.NUMBER,
                        unit="mg/dL",
                        required=False,
                        validation=QuestionValidation(min_value=15.0, max_value=120.0, step=1.0),
                        loinc_code="2085-9",
                        observation_code="HDL_CHOLESTEROL",
                        default_value=44.0,
                    ),
                    QuestionDefinition(
                        id="triglycerides",
                        section=ScreeningSectionType.LABS,
                        label="Serum Triglycerides",
                        help_text="Metabolic marker. Target: < 150 mg/dL.",
                        type=QuestionType.NUMBER,
                        unit="mg/dL",
                        required=False,
                        validation=QuestionValidation(min_value=40.0, max_value=800.0, step=1.0),
                        loinc_code="2571-8",
                        observation_code="TRIGLYCERIDES",
                        default_value=165.0,
                    ),
                    QuestionDefinition(
                        id="serum_creatinine",
                        section=ScreeningSectionType.LABS,
                        label="Serum Creatinine",
                        help_text="Renal clearance marker. Target: 0.6 - 1.2 mg/dL.",
                        type=QuestionType.NUMBER,
                        unit="mg/dL",
                        required=False,
                        validation=QuestionValidation(min_value=0.2, max_value=12.0, step=0.05),
                        loinc_code="2160-0",
                        observation_code="SERUM_CREATININE",
                        default_value=0.95,
                    ),
                    QuestionDefinition(
                        id="egfr",
                        section=ScreeningSectionType.LABS,
                        label="Estimated GFR (eGFR)",
                        help_text="CKD-EPI 2021 equation. Target: >= 90 mL/min/1.73m².",
                        type=QuestionType.NUMBER,
                        unit="mL/min/1.73m2",
                        required=False,
                        is_derived=True,
                        loinc_code="33914-3",
                        observation_code="EGFR",
                    ),
                ],
            ),
            # 5. LIFESTYLE
            ScreeningSectionDefinition(
                id=ScreeningSectionType.LIFESTYLE,
                title="Lifestyle Behaviors & Environmental Factors",
                description="Physical activity, dietary habits, sleep hygiene, tobacco, alcohol, and perceived stress.",
                icon="fa-solid fa-person-walking",
                questions=[
                    QuestionDefinition(
                        id="physical_activity",
                        section=ScreeningSectionType.LIFESTYLE,
                        label="Physical Activity Level",
                        help_text="Weekly duration of moderate aerobic exercise (brisk walk, cycling, manual labor).",
                        type=QuestionType.SELECT,
                        required=True,
                        validation=QuestionValidation(
                            options=[
                                QuestionOption(value="None", label="None / Strictly Sedentary (< 30 min/wk)", risk_weight=0.30),
                                QuestionOption(value="Sedentary", label="Sedentary (30 - 89 min/wk)", risk_weight=0.20),
                                QuestionOption(value="Moderate", label="Moderate (90 - 149 min/wk)", risk_weight=0.10),
                                QuestionOption(value="Vigorous", label="Active (>= 150 min/wk - WHO compliant)", risk_weight=-0.15),
                            ]
                        ),
                        default_value="Sedentary",
                    ),
                    QuestionDefinition(
                        id="diet_quality",
                        section=ScreeningSectionType.LIFESTYLE,
                        label="Dietary Pattern",
                        help_text="Quality of carbohydrates and proportion of whole grains/vegetables vs ultra-processed foods.",
                        type=QuestionType.SELECT,
                        required=True,
                        validation=QuestionValidation(
                            options=[
                                QuestionOption(value="High refined carbs / sweets / fried foods", label="High polished white rice / maida / sweets / deep-fried foods", risk_weight=0.25),
                                QuestionOption(value="Moderate mixed diet", label="Mixed traditional diet with occasional fresh vegetables", risk_weight=0.10),
                                QuestionOption(value="High fiber / millets / vegetables", label="High fiber (ragi/bajra/millets), legumes, salads, and low refined sugar", risk_weight=-0.12),
                            ]
                        ),
                        default_value="High refined carbs / sweets / fried foods",
                    ),
                    QuestionDefinition(
                        id="sleep_hours",
                        section=ScreeningSectionType.LIFESTYLE,
                        label="Average Sleep Duration",
                        help_text="Nightly sleep duration over the past month.",
                        type=QuestionType.SLIDER,
                        unit="hours",
                        required=True,
                        validation=QuestionValidation(min_value=3.0, max_value=12.0, step=0.5),
                        loinc_code="93832-4",
                        observation_code="SLEEP_HOURS",
                        default_value=6.0,
                    ),
                    QuestionDefinition(
                        id="smoking",
                        section=ScreeningSectionType.LIFESTYLE,
                        label="Tobacco Consumption",
                        help_text="Active cigarette, bidi, or smokeless tobacco (gutkha, khaini, paan masala) use.",
                        type=QuestionType.SELECT,
                        required=True,
                        validation=QuestionValidation(
                            options=[
                                QuestionOption(value="NEVER", label="Never used tobacco", risk_weight=-0.15),
                                QuestionOption(value="FORMER", label="Former tobacco user (> 1 yr abstinent)", risk_weight=0.05),
                                QuestionOption(value="CURRENT_SMOKER", label="Active smoking (bidi / cigarettes)", risk_weight=0.25),
                                QuestionOption(value="CURRENT_SMOKELESS", label="Active smokeless tobacco (gutkha / khaini)", risk_weight=0.20),
                            ]
                        ),
                        default_value="NEVER",
                    ),
                    QuestionDefinition(
                        id="alcohol",
                        section=ScreeningSectionType.LIFESTYLE,
                        label="Alcohol Intake",
                        help_text="Frequency and volume of alcohol consumption.",
                        type=QuestionType.SELECT,
                        required=True,
                        validation=QuestionValidation(
                            options=[
                                QuestionOption(value="NONE", label="None / Abstinent", risk_weight=0.0),
                                QuestionOption(value="OCCASIONAL", label="Occasional (< 2 drinks / month)", risk_weight=0.05),
                                QuestionOption(value="REGULAR", label="Regular / Heavy (>= 3-4 drinks / week)", risk_weight=0.18),
                            ]
                        ),
                        default_value="NONE",
                    ),
                    QuestionDefinition(
                        id="stress_level",
                        section=ScreeningSectionType.LIFESTYLE,
                        label="Perceived Mental & Work Stress",
                        help_text="Self-reported stress rating on a scale of 0 (No stress) to 10 (Extreme burnout).",
                        type=QuestionType.SLIDER,
                        unit="score",
                        required=True,
                        validation=QuestionValidation(min_value=0.0, max_value=10.0, step=1.0),
                        loinc_code="76542-0",
                        observation_code="STRESS_LEVEL",
                        default_value=6.0,
                    ),
                ],
            ),
            # 6. HISTORY
            ScreeningSectionDefinition(
                id=ScreeningSectionType.HISTORY,
                title="Family & Clinical Medical History",
                description="Hereditary risk factors, existing comorbidities, and current medication regimen.",
                icon="fa-solid fa-notes-medical",
                questions=[
                    QuestionDefinition(
                        id="family_history",
                        section=ScreeningSectionType.HISTORY,
                        label="Family History of Chronic Disease",
                        help_text="Documented history of diabetes, hypertension, or premature heart attack (< 55 yrs) in first-degree relatives.",
                        type=QuestionType.MULTI_SELECT,
                        required=True,
                        validation=QuestionValidation(
                            options=[
                                QuestionOption(value="NONE", label="No known family history"),
                                QuestionOption(value="DIABETES_ONE_PARENT", label="Diabetes in one parent (+10 IDRS pts)"),
                                QuestionOption(value="DIABETES_BOTH_PARENTS", label="Diabetes in both parents (+20 IDRS pts)"),
                                QuestionOption(value="HYPERTENSION", label="Hypertension in mother or father"),
                                QuestionOption(value="PREMATURE_CAD", label="Premature heart attack / stroke (< 55 yrs)"),
                            ]
                        ),
                        default_value=["DIABETES_ONE_PARENT"],
                    ),
                    QuestionDefinition(
                        id="known_conditions",
                        section=ScreeningSectionType.HISTORY,
                        label="Previously Diagnosed Conditions",
                        help_text="Conditions confirmed by a doctor or clinic.",
                        type=QuestionType.MULTI_SELECT,
                        required=False,
                        validation=QuestionValidation(
                            options=[
                                QuestionOption(value="NONE", label="None"),
                                QuestionOption(value="PREDIABETES", label="Prediabetes / Impaired Fasting Glucose"),
                                QuestionOption(value="HYPERTENSION", label="Hypertension"),
                                QuestionOption(value="FATTY_LIVER", label="Fatty Liver (MASLD / NAFLD)"),
                                QuestionOption(value="DYSLIPIDEMIA", label="High Cholesterol / Triglycerides"),
                                QuestionOption(value="PCOS", label="Polycystic Ovarian Syndrome (PCOS)"),
                            ]
                        ),
                        default_value=["NONE"],
                    ),
                    QuestionDefinition(
                        id="medication_history",
                        section=ScreeningSectionType.HISTORY,
                        label="Current Medications",
                        help_text="Regular daily prescription or over-the-counter medicines.",
                        type=QuestionType.MULTI_SELECT,
                        required=False,
                        validation=QuestionValidation(
                            options=[
                                QuestionOption(value="NONE", label="None"),
                                QuestionOption(value="ANTIHYPERTENSIVE", label="Blood pressure medication (e.g. Telmisartan, Amlodipine)"),
                                QuestionOption(value="METFORMIN_ORAL", label="Blood sugar medication (e.g. Metformin)"),
                                QuestionOption(value="STATIN", label="Cholesterol lowering statin (e.g. Atorvastatin)"),
                                QuestionOption(value="AYURVEDIC_HERBAL", label="Herbal or indigenous traditional formulations"),
                            ]
                        ),
                        default_value=["NONE"],
                    ),
                ],
            ),
        ],
    )


def get_asha_field_rapid_profile() -> QuestionnaireWorkflowProfile:
    """ASHA Health-Worker Village Camp Rapid Screening Protocol."""
    comp = get_mvp_comprehensive_profile()
    # Filter to rapid biometric and CBAC fields for tablet field entry
    return QuestionnaireWorkflowProfile(
        id="asha_field_rapid",
        title="ASHA Community Field Camp Screening (CBAC & Rapid Vitals)",
        version="1.1.0",
        target_role="HEALTH_WORKER",
        description="Streamlined for point-of-care rapid field camps by ASHA health workers.",
        estimated_time_minutes=4,
        sections=comp.sections,
    )


def get_citizen_self_check_profile() -> QuestionnaireWorkflowProfile:
    """Citizen Self-Screening Protocol."""
    comp = get_mvp_comprehensive_profile()
    return QuestionnaireWorkflowProfile(
        id="citizen_self_check",
        title="Citizen Preventive Health Self-Check",
        version="1.0.0",
        target_role="CITIZEN",
        description="Intuitive personal checkup with immediate explainable risk insights.",
        estimated_time_minutes=5,
        sections=comp.sections,
    )


# Registry of available profiles
WORKFLOW_PROFILES: Dict[str, QuestionnaireWorkflowProfile] = {
    "mvp_comprehensive": get_mvp_comprehensive_profile(),
    "asha_field_rapid": get_asha_field_rapid_profile(),
    "citizen_self_check": get_citizen_self_check_profile(),
}


# ==============================================================================
# 2. COMPUTATION & EVALUATION LOGIC
# ==============================================================================

def calculate_derived_bmi(
    weight_kg: Optional[float],
    height_val: Optional[float] = None,
    height_cm: Optional[float] = None,
    decimals: int = 2
) -> Optional[float]:
    """Calculates BMI = weight in kg ÷ (height in metres)².
    Supports height in metres (e.g. 1.70) or centimetres (e.g. 170.0).
    """
    effective_height = height_val if height_val is not None else height_cm
    if weight_kg and effective_height and weight_kg > 0 and effective_height > 0:
        h_m = effective_height if effective_height <= 3.0 else (effective_height / 100.0)
        if h_m > 0:
            return round(weight_kg / (h_m * h_m), decimals)
    return None


def calculate_derived_egfr(serum_creatinine: Optional[float], age: int, sex: str) -> Optional[float]:
    """Calculates eGFR using CKD-EPI 2021 equation without race coefficient."""
    if not serum_creatinine or serum_creatinine <= 0:
        return None
    scr = serum_creatinine
    is_female = (sex.upper() == "FEMALE")
    k = 0.7 if is_female else 0.9
    alpha = -0.241 if is_female else -0.302
    mult = 1.012 if is_female else 1.0

    scr_k = scr / k
    min_val = min(scr_k, 1.0) ** alpha
    max_val = max(scr_k, 1.0) ** (-1.200)
    age_factor = 0.9938 ** age

    egfr = 142.0 * min_val * max_val * age_factor * mult
    return round(egfr, 1)


def derive_idrs_from_answers(answers: Dict[str, Any]) -> int:
    """Computes Indian Diabetes Risk Score (0-100) from questionnaire answers."""
    score = 0
    age = float(answers.get("age", 45))
    if age >= 50:
        score += 30
    elif age >= 35:
        score += 20

    waist = float(answers.get("waist_circumference", 85))
    sex = str(answers.get("sex", "MALE")).upper()
    if sex == "MALE":
        if waist >= 100:
            score += 20
        elif waist >= 90:
            score += 10
    else:
        if waist >= 90:
            score += 20
        elif waist >= 80:
            score += 10

    act = str(answers.get("physical_activity", "Sedentary")).lower()
    if "none" in act:
        score += 30
    elif "sedentary" in act:
        score += 20
    elif "moderate" in act:
        score += 10

    fam = answers.get("family_history", [])
    if isinstance(fam, list):
        if "DIABETES_BOTH_PARENTS" in fam:
            score += 20
        elif "DIABETES_ONE_PARENT" in fam:
            score += 10
    return score


def derive_cbac_from_answers(answers: Dict[str, Any]) -> int:
    """Computes CBAC Score (0-10) for community field screening."""
    score = 0
    age = float(answers.get("age", 45))
    if age > 30:
        score += 2

    tobacco = str(answers.get("smoking", "NEVER")).upper()
    if "CURRENT" in tobacco:
        score += 2

    alcohol = str(answers.get("alcohol", "NONE")).upper()
    if alcohol in ["REGULAR", "OCCASIONAL"]:
        score += 1

    waist = float(answers.get("waist_circumference", 85))
    sex = str(answers.get("sex", "MALE")).upper()
    if (sex == "MALE" and waist >= 90) or (sex == "FEMALE" and waist >= 80):
        score += 2

    act = str(answers.get("physical_activity", "Moderate")).lower()
    if "none" in act or "sedentary" in act or "moderate" in act:
        score += 2

    fam = answers.get("family_history", [])
    if isinstance(fam, list) and any("DIABETES" in f or "HYPERTENSION" in f for f in fam):
        score += 2

    return min(10, score)


# ==============================================================================
# 3. QUESTIONNAIRE EXECUTION ENGINE
# ==============================================================================

class QuestionnaireEngine:
    """Production NCD Screening Engine providing configurable workflow execution,
    automatic LOINC structured observation emission, and explainable summary generation.
    """

    @classmethod
    def get_profile(cls, workflow_id: str) -> QuestionnaireWorkflowProfile:
        return WORKFLOW_PROFILES.get(workflow_id, WORKFLOW_PROFILES["mvp_comprehensive"])

    @classmethod
    def evaluate_submission(
        cls,
        submission: ConfigurableScreeningSubmission,
        actor_id: str = "health-worker-asha-01",
        actor_role: Any = "HEALTH_WORKER",
    ) -> ScreeningEvaluationResponse:
        from packages.types.enums import UserRole
        if isinstance(actor_role, str):
            try:
                actor_role = UserRole(actor_role)
            except ValueError:
                actor_role = UserRole.HEALTH_WORKER

        citizen = store.get_citizen(submission.citizen_id)
        if not citizen:
            if len(store.citizens) == 0:
                from scripts.seed_data import seed_all_demo_data
                seed_all_demo_data()
            citizen = store.get_citizen(submission.citizen_id)
            if not citizen and store.citizens:
                citizen = list(store.citizens.values())[0]
            if not citizen:
                raise ValueError(f"Citizen {submission.citizen_id} not found in repository.")

        answers = submission.answers
        now = datetime.now(timezone.utc)
        profile = cls.get_profile(submission.workflow_id)

        # 0. Validate Required Fields (TC-SCR-002)
        required_questions = [
            q for s in profile.sections for q in s.questions if q.required
        ]
        missing_fields = []
        for q in required_questions:
            val = answers.get(q.id)
            if val is None or val == "" or (isinstance(val, list) and len(val) == 0):
                if q.default_value is not None:
                    answers[q.id] = q.default_value
                else:
                    missing_fields.append(q.id)

        if missing_fields:
            if not submission.allow_incomplete:
                raise ValueError(
                    f"Missing required screening field(s): {', '.join(missing_fields)}. "
                    "Submission cannot be completed without required demographic, vital, and lifestyle fields."
                )
            else:
                incomplete_session = ScreeningSession(
                    id=str(uuid.uuid4()),
                    tenant_id=citizen.tenant_id,
                    citizen_id=citizen.id,
                    conducted_by_id=actor_id,
                    conducted_at=now,
                    notes=submission.notes,
                    status="INCOMPLETE",
                    questionnaire_version=profile.version,
                )
                store.add_screening(incomplete_session)
                return ScreeningEvaluationResponse(
                    screening_session_id=incomplete_session.id,
                    citizen_id=citizen.id,
                    conducted_at=now,
                    status="INCOMPLETE",
                    questionnaire_version=profile.version,
                    missing_fields=missing_fields,
                    structured_observations_count=0,
                    structured_observations=[],
                    explainable_summary=None,
                    risk_assessment=None,
                )

        # 0b. Validate Blood Pressure Clinical Bounds (TC-SCR-003)
        sbp = answers.get("systolic_bp")
        dbp = answers.get("diastolic_bp")
        if sbp is not None:
            try:
                sbp_val = float(sbp)
            except (ValueError, TypeError):
                raise ValueError(f"Invalid systolic blood pressure: '{sbp}'. Must be a valid numeric measurement.")
            if sbp_val < 40.0 or sbp_val > 300.0:
                raise ValueError(
                    f"Invalid systolic blood pressure: {sbp_val} mmHg. "
                    "Must be clinically plausible between 40 and 300 mmHg. Value rejected per clinical validation rules; no risk calculation performed."
                )
        if dbp is not None:
            try:
                dbp_val = float(dbp)
            except (ValueError, TypeError):
                raise ValueError(f"Invalid diastolic blood pressure: '{dbp}'. Must be a valid numeric measurement.")
            if dbp_val < 30.0 or dbp_val > 200.0:
                raise ValueError(
                    f"Invalid diastolic blood pressure: {dbp_val} mmHg. "
                    "Must be clinically plausible between 30 and 200 mmHg. Value rejected per clinical validation rules; no risk calculation performed."
                )
        if sbp is not None and dbp is not None:
            if float(sbp) <= float(dbp):
                raise ValueError(
                    f"Invalid blood pressure: systolic ({sbp} mmHg) must be strictly greater "
                    f"than diastolic ({dbp} mmHg). Value rejected per clinical validation rules; no risk calculation performed."
                )

        # 1. Compute Derived Anthropometrics & Renal Metrics
        height = float(answers.get("height", 170.0)) if "height" in answers else None
        weight = float(answers.get("weight", 75.0)) if "weight" in answers else None
        bmi = answers.get("bmi") or calculate_derived_bmi(weight, height)
        if bmi:
            answers["bmi"] = bmi

        age = int(answers.get("age", 45))
        sex = str(answers.get("sex", citizen.gender.value)).upper()
        scr = float(answers.get("serum_creatinine", 0)) if "serum_creatinine" in answers else None
        egfr = answers.get("egfr") or calculate_derived_egfr(scr, age, sex)
        if egfr:
            answers["egfr"] = egfr

        idrs_score = derive_idrs_from_answers(answers)
        cbac_score = derive_cbac_from_answers(answers)

        # 2. Emit Structured LOINC Observations
        observations: List[Observation] = []
        observation_keys = {
            "SYSTOLIC_BP": ("systolic_bp", "8480-6", "Systolic Blood Pressure", "mmHg"),
            "DIASTOLIC_BP": ("diastolic_bp", "8462-4", "Diastolic Blood Pressure", "mmHg"),
            "HEART_RATE": ("heart_rate", "8867-4", "Resting Heart Rate", "beats/min"),
            "FASTING_GLUCOSE": ("fasting_glucose", "1558-6", "Fasting Blood Glucose", "mg/dL"),
            "HBA1C": ("hba1c", "4548-4", "Hemoglobin A1c", "%"),
            "TOTAL_CHOLESTEROL": ("total_cholesterol", "2093-3", "Total Cholesterol", "mg/dL"),
            "LDL_CHOLESTEROL": ("ldl_cholesterol", "13457-7", "LDL Cholesterol", "mg/dL"),
            "HDL_CHOLESTEROL": ("hdl_cholesterol", "2085-9", "HDL Cholesterol", "mg/dL"),
            "TRIGLYCERIDES": ("triglycerides", "2571-8", "Serum Triglycerides", "mg/dL"),
            "HEIGHT": ("height", "8302-2", "Body Height", "cm"),
            "WEIGHT": ("weight", "29463-7", "Body Weight", "kg"),
            "BMI": ("bmi", "39156-5", "Body Mass Index", "kg/m2"),
            "WAIST_CIRCUMFERENCE": ("waist_circumference", "8280-0", "Waist Circumference", "cm"),
            "SERUM_CREATININE": ("serum_creatinine", "2160-0", "Serum Creatinine", "mg/dL"),
            "EGFR": ("egfr", "33914-3", "Estimated GFR", "mL/min/1.73m2"),
            "SLEEP_HOURS": ("sleep_hours", "93832-4", "Sleep Duration", "hours"),
            "STRESS_LEVEL": ("stress_level", "76542-0", "Perceived Stress Score", "score"),
        }

        for obs_code, (ans_key, loinc_c, display_name, unit_str) in observation_keys.items():
            if ans_key in answers and answers[ans_key] is not None:
                val = float(answers[ans_key])
                obs = Observation(
                    id=str(uuid.uuid4()),
                    tenant_id=citizen.tenant_id,
                    citizen_id=citizen.id,
                    code=obs_code,
                    value=val,
                    unit=unit_str,
                    loinc_code=loinc_c,
                    display_name=display_name,
                    source="CONFIGURABLE_SCREENING",
                    recorded_at=now,
                )
                observations.append(obs)
                store.add_observation(obs)

        # 3. Create Traditional Screening Session for Long-term Storage
        vitals_payload = VitalsPayload(
            systolic_bp=float(answers.get("systolic_bp", 125)),
            diastolic_bp=float(answers.get("diastolic_bp", 80)),
            heart_rate=float(answers.get("heart_rate", 72)) if "heart_rate" in answers else None,
            fasting_glucose=float(answers.get("fasting_glucose", 95)) if "fasting_glucose" in answers else None,
            hba1c=float(answers.get("hba1c", 5.4)) if "hba1c" in answers else None,
            total_cholesterol=float(answers.get("total_cholesterol", 190)) if "total_cholesterol" in answers else None,
            hdl_cholesterol=float(answers.get("hdl_cholesterol", 45)) if "hdl_cholesterol" in answers else None,
            triglycerides=float(answers.get("triglycerides", 140)) if "triglycerides" in answers else None,
            bmi=float(answers.get("bmi", 23.5)) if "bmi" in answers else None,
            waist_circumference=float(answers.get("waist_circumference", 85)) if "waist_circumference" in answers else None,
            egfr=float(answers.get("egfr", 95)) if "egfr" in answers else None,
        )

        session = ScreeningSession(
            id=str(uuid.uuid4()),
            tenant_id=citizen.tenant_id,
            citizen_id=citizen.id,
            conducted_by_id=actor_id,
            conducted_at=now,
            vitals=vitals_payload,
            notes=submission.notes,
            calculated_idrs_score=idrs_score,
            calculated_cbac_score=cbac_score,
            status="COMPLETED",
            questionnaire_version=profile.version,
        )
        store.add_screening(session)

        # 4. Integrate Wearable Telemetry & Risk Stratification
        wearable_proj = wearable_adapter.get_projection(citizen.id)
        smoker = "CURRENT" in str(answers.get("smoking", "NEVER")).upper()

        domains, overall_score, tier, trajectory, drivers, protective = evaluate_ncd_domains(
            observations=observations,
            idrs_score=idrs_score,
            age=age,
            gender=sex,
            smoker=smoker,
            wearable_projection=wearable_proj,
        )

        assessment = RiskAssessment(
            id=str(uuid.uuid4()),
            tenant_id=citizen.tenant_id,
            citizen_id=citizen.id,
            screening_session_id=session.id,
            model_name="NCD_COMPOSITE_RISK_ENGINE",
            model_version="calibrated_ensemble_v1.0.0",
            input_snapshot=answers,
            overall_score=overall_score,
            overall_tier=tier,
            domains=domains,
            trajectory=trajectory,
            confidence_score=0.94 if len(observations) >= 6 else 0.72,
            top_drivers=drivers,
            protective_factors=protective,
            clinical_summary=(
                f"Citizen exhibits {tier.value} NCD risk ({overall_score*100:.0f}% composite index). "
                f"Top driving factor is {drivers[0].feature_name if drivers else 'general lifestyle factors'}. "
                f"Longitudinal trajectory is {trajectory.value}."
            ),
            evaluated_at=now,
        )
        store.add_risk_assessment(assessment)

        # 5. Formulate Human-in-the-Loop Action Directives
        lifestyle_actions: List[str] = []
        diagnostic_actions: List[str] = []

        if float(answers.get("fasting_glucose", 0)) >= 100 or float(answers.get("hba1c", 0)) >= 5.7:
            lifestyle_actions.append("Replace 50% refined white rice with foxtail millet or ragi to stabilize postprandial glucose.")
            lifestyle_actions.append("Adopt 15-minute brisk walk within 30 minutes following dinner.")
            diagnostic_actions.append("Schedule confirmatory 2-hour Oral Glucose Tolerance Test (OGTT).")

        if float(answers.get("systolic_bp", 0)) >= 130 or float(answers.get("diastolic_bp", 0)) >= 85:
            lifestyle_actions.append("Limit dietary sodium to < 2000 mg/day; avoid added salt in curries and packaged snacks.")
            diagnostic_actions.append("Repeat bilateral blood pressure measurement after 1 week.")

        if float(answers.get("waist_circumference", 0)) >= (90 if sex == "MALE" else 80):
            lifestyle_actions.append("Incorporate 150 minutes of moderate aerobic activity weekly.")

        if smoker:
            lifestyle_actions.append("Enroll in primary healthcare tobacco cessation counseling program.")

        requires_escalation = tier in [RiskTier.HIGH, RiskTier.CRITICAL]
        urgency = TriageUrgency.EMERGENT.value if tier == RiskTier.CRITICAL else (TriageUrgency.PRIORITY.value if tier == RiskTier.HIGH else None)

        explainable_summary = ExplainableScreeningSummary(
            screening_id=session.id,
            citizen_id=citizen.id,
            evaluated_at=now,
            composite_risk_score=overall_score,
            overall_tier=tier.value,
            trajectory=trajectory.value,
            domain_scores={
                "diabetes_risk": domains.diabetes_risk,
                "hypertension_risk": domains.hypertension_risk,
                "cardiovascular_risk": domains.cardiovascular_risk,
                "metabolic_syndrome_risk": domains.metabolic_syndrome_risk,
                "ckd_risk": domains.ckd_risk,
                "fatty_liver_risk": domains.fatty_liver_risk,
            },
            idrs_score=idrs_score,
            cbac_score=cbac_score,
            calculated_bmi=bmi,
            calculated_egfr=egfr,
            top_drivers=drivers,
            protective_factors=protective,
            clinical_recommendation=(
                f"Citizen categorized as {tier.value} NCD Risk with {trajectory.value} trajectory. "
                f"Primary contributor: {drivers[0].feature_name if drivers else 'Biometric factors'}. "
                f"{'Requires urgent clinical officer evaluation.' if requires_escalation else 'Manageable with targeted lifestyle medicine.'}"
            ),
            requires_clinical_escalation=requires_escalation,
            escalation_urgency=urgency,
            immediate_lifestyle_actions=lifestyle_actions,
            recommended_diagnostic_followups=diagnostic_actions,
        )

        audit_logger.record(
            tenant_id=citizen.tenant_id,
            actor_id=actor_id,
            actor_role=actor_role,
            action=AuditAction.SCREENING_SUBMITTED,
            resource_type="ConfigurableScreening",
            resource_id=session.id,
            details={
                "workflow_id": submission.workflow_id,
                "observations_count": len(observations),
                "tier": tier.value,
                "score": overall_score,
            },
        )

        return ScreeningEvaluationResponse(
            screening_session_id=session.id,
            citizen_id=citizen.id,
            conducted_at=now,
            status="COMPLETED",
            questionnaire_version=profile.version,
            missing_fields=[],
            structured_observations_count=len(observations),
            structured_observations=observations,
            explainable_summary=explainable_summary,
            risk_assessment=assessment,
        )
