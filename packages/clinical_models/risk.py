from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import uuid

from packages.types.enums import RiskTier, TrajectoryTrend


class DomainRiskScores(BaseModel):
    diabetes_risk: Optional[float] = None              # 0.0 to 1.0 (None when INSUFFICIENT_DATA)
    hypertension_risk: Optional[float] = None          # 0.0 to 1.0 (None when INSUFFICIENT_DATA)
    cardiovascular_risk: Optional[float] = None        # 0.0 to 1.0 (None when INSUFFICIENT_DATA)
    metabolic_syndrome_risk: Optional[float] = None    # 0.0 to 1.0 (None when INSUFFICIENT_DATA)
    ckd_risk: Optional[float] = None                   # 0.0 to 1.0 (None when INSUFFICIENT_DATA)
    fatty_liver_risk: Optional[float] = None           # 0.0 to 1.0 (None when INSUFFICIENT_DATA)


class RiskDriver(BaseModel):
    """Explainable feature driver (+% risk contribution)."""
    feature_name: str
    observed_value: str
    target_value: str
    impact_weight: float              # e.g. +0.24 (+24%)
    category: str = "BIOMETRIC"       # "BIOMETRIC", "LIFESTYLE", "GENETIC"
    evidence_citation: str = "ICMR Guidelines / Clinical Evidence"


class ProtectiveFactor(BaseModel):
    """Explainable protective factor (-% risk reduction)."""
    feature_name: str
    observed_value: str
    impact_weight: float              # e.g. -0.10 (-10%)
    category: str = "LIFESTYLE"


class RiskAssessment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    citizen_id: str
    screening_session_id: Optional[str] = None
    model_name: str = "NCD_COMPOSITE_RISK_ENGINE"
    model_version: str = "calibrated_ensemble_v1.0.0"
    input_snapshot: Optional[Dict[str, Any]] = None
    overall_score: Optional[float] = None              # 0.0 to 1.0 (None when INSUFFICIENT_DATA)
    overall_tier: RiskTier
    domains: DomainRiskScores
    trajectory: TrajectoryTrend
    confidence_score: float           # 0.0 to 1.0 (completeness of lab/biometric data)
    top_drivers: List[RiskDriver] = []
    protective_factors: List[ProtectiveFactor] = []
    missing_requirements: List[str] = []
    limitations: List[str] = [
        "Statistical risk assessment tool; clinical diagnosis requires formal laboratory testing.",
        "Capillary point-of-care glucometers have +/- 15% variance compared to venous laboratory assays.",
        "10-year risk projections assume unmanaged trajectory without preventive lifestyle intervention."
    ]
    is_clinically_validated: bool = True
    is_demonstration_only: bool = False
    validation_status: str = "CLINICALLY_VALIDATED"
    demonstration_warning: Optional[str] = None
    clinical_summary: str
    safety_disclaimer: str = (
        "CLINICAL REVIEW RECOMMENDED: This assessment is an AI-assisted preventive health screening "
        "tool based on population guidelines. It does NOT constitute a medical diagnosis or treatment plan."
    )
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Backward-compatible model aliases
RiskDomains = DomainRiskScores
RiskFactorDriver = RiskDriver

