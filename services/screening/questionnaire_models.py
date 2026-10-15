from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Union
from enum import Enum
from pydantic import BaseModel, Field
import uuid

from packages.clinical_models.observations import Observation
from packages.clinical_models.risk import RiskAssessment, RiskDriver, ProtectiveFactor


class QuestionType(str, Enum):
    NUMBER = "NUMBER"
    TEXT = "TEXT"
    SELECT = "SELECT"
    MULTI_SELECT = "MULTI_SELECT"
    BOOLEAN = "BOOLEAN"
    SLIDER = "SLIDER"
    DATE = "DATE"


class ScreeningSectionType(str, Enum):
    DEMOGRAPHICS = "DEMOGRAPHICS"
    ANTHROPOMETRY = "ANTHROPOMETRY"
    VITALS = "VITALS"
    LABS = "LABS"
    LIFESTYLE = "LIFESTYLE"
    HISTORY = "HISTORY"


class QuestionOption(BaseModel):
    value: Union[str, int, float]
    label: str
    description: Optional[str] = None
    risk_weight: Optional[float] = 0.0


class QuestionValidation(BaseModel):
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    step: Optional[float] = None
    options: Optional[List[QuestionOption]] = None
    pattern: Optional[str] = None


class QuestionDefinition(BaseModel):
    id: str                                  # e.g. "systolic_bp", "hba1c", "diet_quality"
    section: ScreeningSectionType
    label: str                                # Human readable title
    help_text: Optional[str] = None           # Clinical guidance or context
    type: QuestionType
    unit: Optional[str] = None                # e.g. "mmHg", "mg/dL", "cm", "kg"
    required: bool = False
    validation: Optional[QuestionValidation] = None
    loinc_code: Optional[str] = None          # LOINC code for mapping to structured observation
    observation_code: Optional[str] = None    # LOINC_CODES registry key (e.g. "SYSTOLIC_BP")
    default_value: Optional[Any] = None
    role_visibility: List[str] = ["CITIZEN", "HEALTH_WORKER", "CLINICIAN"]
    is_derived: bool = False                  # e.g., BMI, eGFR, IDRS score, CBAC score


class ScreeningSectionDefinition(BaseModel):
    id: ScreeningSectionType
    title: str
    description: str
    icon: Optional[str] = None
    questions: List[QuestionDefinition]


class QuestionnaireWorkflowProfile(BaseModel):
    id: str                                  # e.g., "mvp_comprehensive", "asha_field_rapid", "citizen_self_check"
    title: str
    version: str = "1.0.0"
    target_role: str                         # "ALL" | "HEALTH_WORKER" | "CITIZEN" | "CLINICIAN"
    description: str
    estimated_time_minutes: int = 5
    sections: List[ScreeningSectionDefinition]


class ConfigurableScreeningSubmission(BaseModel):
    citizen_id: str
    workflow_id: str = "mvp_comprehensive"
    answers: Dict[str, Any]                  # key: question_id -> value
    notes: Optional[str] = None
    screening_location: Optional[str] = "COMMUNITY_FIELD_CAMP"
    allow_incomplete: bool = False           # If True, records as INCOMPLETE state instead of rejection


class ExplainableScreeningSummary(BaseModel):
    screening_id: str
    citizen_id: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Stratification Output
    composite_risk_score: float              # 0.0 to 1.0
    overall_tier: str                         # LOW | MODERATE | HIGH | CRITICAL
    trajectory: str                           # IMPROVING | STABLE | DETERIORATING
    
    # Quantitative Breakdown
    domain_scores: Dict[str, float]           # diabetes, hypertension, cardiovascular, metabolic, ckd, fatty_liver
    idrs_score: Optional[int] = None
    cbac_score: Optional[int] = None
    calculated_bmi: Optional[float] = None
    calculated_egfr: Optional[float] = None
    
    # Explainable Drivers & Factors
    top_drivers: List[RiskDriver] = []
    protective_factors: List[ProtectiveFactor] = []
    
    # Human-in-the-Loop Actionable Directives
    clinical_recommendation: str
    requires_clinical_escalation: bool = False
    escalation_urgency: Optional[str] = None  # ROUTINE | PRIORITY | EMERGENT
    immediate_lifestyle_actions: List[str] = []
    recommended_diagnostic_followups: List[str] = []
    
    # Safety Boundary Disclaimer
    clinical_safety_notice: str = (
        "PREVENTIVE HEALTH DECISION SUPPORT NOTICE: This screening evaluates probabilistic risk "
        "and lifestyle trajectories. It does NOT constitute a confirmed medical diagnosis or prescription. "
        "High-risk assessments must be verified by a qualified medical professional."
    )


class ScreeningEvaluationResponse(BaseModel):
    screening_session_id: str
    citizen_id: str
    conducted_at: datetime
    status: str = "COMPLETED"                 # COMPLETED | INCOMPLETE | FLAGGED_FOR_REVIEW
    questionnaire_version: str = "1.2.0"
    missing_fields: List[str] = []
    structured_observations_count: int
    structured_observations: List[Observation]
    explainable_summary: Optional[ExplainableScreeningSummary] = None
    risk_assessment: Optional[RiskAssessment] = None

