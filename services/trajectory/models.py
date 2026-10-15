from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field
import uuid


class TrajectoryTrendState(str, Enum):
    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    WORSENING = "WORSENING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class TrajectoryDomain(str, Enum):
    METABOLIC = "metabolic"
    DIABETES = "diabetes"
    HYPERTENSION = "hypertension"
    CARDIOVASCULAR = "cardiovascular"
    OBESITY = "obesity"
    RENAL = "renal"
    LIFESTYLE = "lifestyle"


class RiskSnapshot(BaseModel):
    """Point-in-time cross-domain clinical risk state."""
    snapshot_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    citizen_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    domain_scores: Dict[str, Optional[float]] = {}  # 0.0 to 1.0 (or None if insufficient)
    domain_tiers: Dict[str, str] = {}               # LOW | MODERATE | HIGH | CRITICAL | INSUFFICIENT_DATA
    key_biomarkers: Dict[str, Any] = {}             # SBP, DBP, HBA1C, FBG, BMI, WAIST, STEPS, HRV
    source: str = "SCREENING"                       # SCREENING | LAB_PANEL | WEARABLE_AGGREGATE | CLINICAL_REVIEW
    data_completeness: float = 1.0                  # Ratio of observed metrics (0.0 - 1.0)
    confidence: float = 0.90                        # Sensor and assay confidence (0.0 - 1.0)
    
    # Model provenance and scoring scale specifications (TC-TRJ-003)
    model_name: str = "NCD_COMPOSITE_RISK_ENGINE"
    model_version: str = "calibrated_ensemble_v1.0.0"
    scoring_scale: str = "PROBABILITY_0_1"          # PROBABILITY_0_1 | INTEGER_0_100 | POINTS_0_50 | ORDINAL_SCORE
    is_clinically_validated: bool = True
    is_demonstration_only: bool = False


class DomainTrajectoryComparison(BaseModel):
    """Detailed three-way comparison (Current vs Previous vs Baseline) for a clinical domain."""
    domain: str
    current_score: Optional[float] = None
    current_tier: str = "INSUFFICIENT_DATA"
    
    previous_score: Optional[float] = None
    previous_tier: Optional[str] = None
    
    baseline_score: Optional[float] = None
    baseline_tier: Optional[str] = None
    
    trend: TrajectoryTrendState
    
    # Mathematical delta calculations
    change_percentage_from_baseline: Optional[float] = None  # % change relative to baseline score
    absolute_delta_from_previous: Optional[float] = None     # Current - Previous probability delta
    absolute_delta_from_baseline: Optional[float] = None     # Current - Baseline probability delta
    
    # Explainable drivers strictly using non-causal language
    contributing_factors: List[str] = []
    
    confidence: float = 0.85
    data_completeness: float = 1.0
    clinical_note: str = ""
    
    # Model comparability indicator
    is_comparable: bool = True
    comparison_note: Optional[str] = None


class CitizenTrajectoryReport(BaseModel):
    """Longitudinal multi-domain risk trajectory for a citizen."""
    citizen_id: str
    calculated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_snapshots: int
    baseline_snapshot: Optional[RiskSnapshot] = None
    previous_snapshot: Optional[RiskSnapshot] = None
    current_snapshot: Optional[RiskSnapshot] = None
    historical_snapshots: List[RiskSnapshot] = []
    
    # The 7 mandatory domain trajectories
    domain_trajectories: Dict[str, DomainTrajectoryComparison]
    
    overall_trend: TrajectoryTrendState
    composite_current_score: Optional[float] = None
    composite_baseline_score: Optional[float] = None
    composite_change_percentage: Optional[float] = None
    
    clinical_summary: str
    clinical_safety_notice: str = (
        "SAFETY NOTICE: Risk trajectories represent observational longitudinal comparisons across "
        "guideline-based risk estimates. Observed correlations do not prove causality. "
        "Any therapeutic or medication modifications require physician consultation."
    )
    
    # Model comparability flags & limitation notices (TC-TRJ-003)
    is_comparable: bool = True
    numerical_comparison_refused: bool = False
    comparison_limitations: List[str] = []
    
    # Review workflow behavior flags (TC-TRJ-002)
    review_required: bool = False
    review_workflow_status: str = "STANDARD"  # STANDARD | CLINICIAN_REVIEW_REQUIRED | ESCALATED_TRIAGE
    review_priority: Optional[str] = None     # ROUTINE | PRIORITY | EMERGENT
    triage_case_id: Optional[str] = None
    recommended_review_actions: List[str] = []


class TrajectoryComparisonRequest(BaseModel):
    citizen_id: str
    baseline_snapshot_id: Optional[str] = None
    current_snapshot_id: Optional[str] = None
    strict: bool = False


class TrajectoryComparisonResponse(BaseModel):
    citizen_id: str
    is_comparable: bool
    numerical_comparison_refused: bool
    limitation_reason: Optional[str] = None
    baseline_version: Optional[str] = None
    current_version: Optional[str] = None
    baseline_scale: Optional[str] = None
    current_scale: Optional[str] = None
    report: Optional[CitizenTrajectoryReport] = None
