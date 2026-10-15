"""Data Models for SevaHealth Population Health Intelligence."""

from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class PopulationOverviewResponse(BaseModel):
    state: str = "Karnataka"
    selected_program: str
    selected_district: str
    total_target_population: int
    population_screened: int
    screening_completion_rate_pct: float
    high_risk_population: int
    high_risk_percentage: float
    diabetes_risk_prevalence_pct: float
    hypertension_risk_prevalence_pct: float
    cardiovascular_risk_prevalence_pct: float
    obesity_risk_prevalence_pct: float
    followup_completion_rate_pct: float
    intervention_adherence_mean_pct: float
    referral_completion_rate_pct: float
    risk_improvement_count: int
    active_care_plans: int
    demographic_summary: Dict[str, Any]
    privacy_notice: str = (
        "CONFIDENTIAL & DE-IDENTIFIED: Minimum aggregation threshold k >= 10 enforced. "
        "No individual patient PII is accessible through this dashboard."
    )
    is_simulated: bool = True
    data_provenance: str = "SYNTHETIC_DEMONSTRATION_DATA"
    provenance_disclaimer: str = (
        "DEMONSTRATION ONLY: Synthetic cohort data generated for evaluation and training. "
        "Not representative of actual clinical or community outcomes."
    )


class RiskDistributionDomain(BaseModel):
    domain_name: str
    prevalence_rate_pct: float
    low_count: int
    moderate_count: int
    high_count: int
    critical_count: int
    insufficient_data_count: int = 0
    leading_risk_driver: str


class RiskDistributionResponse(BaseModel):
    total_screened: int
    overall_tiers: Dict[str, int]
    insufficient_data_count: int = 0
    domains: List[RiskDistributionDomain]
    demographic_cross_tabulation: Dict[str, Any]
    is_simulated: bool = True
    data_provenance: str = "SYNTHETIC_DEMONSTRATION_DATA"
    provenance_disclaimer: str = (
        "DEMONSTRATION ONLY: Synthetic cohort data generated for evaluation and training. "
        "Not representative of actual clinical or community outcomes."
    )


class TimeTrendPoint(BaseModel):
    period: str                         # e.g., "2026-05", "2026-06", etc.
    screened_cumulative: int
    high_risk_detected: int
    high_risk_pct: float
    care_plans_activated: int
    mean_adherence_pct: float
    risk_tier_downgrades: int


class RiskTrendsResponse(BaseModel):
    reporting_window: str = "Last 6 Months (Longitudinal)"
    monthly_trends: List[TimeTrendPoint]
    key_findings: List[str]
    is_simulated: bool = True
    data_provenance: str = "SYNTHETIC_DEMONSTRATION_DATA"
    provenance_disclaimer: str = (
        "DEMONSTRATION ONLY: Synthetic cohort data generated for evaluation and training. "
        "Not representative of actual clinical or community outcomes."
    )


class HighRiskCohortItem(BaseModel):
    cohort_id: str
    cohort_name: str
    size: int
    prevalence_pct: float
    dominant_demographics: str
    leading_biomarkers: List[str]
    top_lifestyle_contributors: List[str]
    suggested_public_health_intervention: str
    is_suppressed: bool = False


class HighRiskCohortsResponse(BaseModel):
    total_high_risk_citizens: int
    k_anonymity_threshold: int = 10
    cohorts: List[HighRiskCohortItem]
    targeted_action_recommendations: List[str]
    is_simulated: bool = True
    data_provenance: str = "SYNTHETIC_DEMONSTRATION_DATA"
    provenance_disclaimer: str = (
        "DEMONSTRATION ONLY: Synthetic cohort data generated for evaluation and training. "
        "Not representative of actual clinical or community outcomes."
    )


class InterventionOutcomesResponse(BaseModel):
    analysis_cohort_size: int
    evaluation_protocol: str
    high_adherence_group: Dict[str, Any]
    low_adherence_group: Dict[str, Any]
    mean_systolic_bp_reduction_mmhg: float
    mean_fasting_glucose_reduction_mgdl: float
    total_risk_tier_downgrades: int
    p_value: str
    clinical_inference: str


class ReferralFunnelStage(BaseModel):
    stage_id: str
    stage_name: str
    count: int
    conversion_rate_pct: float
    average_lag_days: float


class ReferralPipelineResponse(BaseModel):
    total_referrals_initiated: int
    funnel_stages: List[ReferralFunnelStage]
    facility_breakdown: List[Dict[str, Any]]
    urgency_breakdown: Dict[str, int]
    pipeline_bottlenecks: List[str]


class CommunityComparisonItem(BaseModel):
    community_id: str
    community_name: str
    level: str                          # DISTRICT | TALUK | WARD
    target_population: int
    screened_population: int
    screening_completion_pct: float
    high_risk_pct: float
    diabetes_prevalence_pct: float
    hypertension_prevalence_pct: float
    care_plan_adherence_pct: float
    referral_completion_pct: float
    performance_tier: str               # HIGH_PERFORMING | AVERAGE | PRIORITY_NEEDS_FOCUS


class CommunityComparisonResponse(BaseModel):
    benchmark_metric: str = "Comprehensive Ayushman Bharat Performance Index"
    communities: List[CommunityComparisonItem]
    top_performing_districts: List[str]
    priority_action_districts: List[str]


class DeidentifiedExportRow(BaseModel):
    pseudonym_id: str
    age_group: str
    sex: str
    district: str
    composite_risk_tier: str
    blood_pressure_category: str
    glucose_category: str
    bmi_category: str
    care_plan_enrolled: bool
    adherence_tier: str
    data_provenance: str = "SYNTHETIC_DEMONSTRATION_DATA"


class DeidentifiedExportResponse(BaseModel):
    sample_size: int
    export_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deidentification_method: str = "Salted SHA-256 Pseudonymization + 5-Year Age Banding + Binning"
    k_anonymity_guarantee: str = "k >= 10"
    records: List[DeidentifiedExportRow]
    is_simulated: bool = True
    data_provenance: str = "SYNTHETIC_DEMONSTRATION_DATA"
    provenance_disclaimer: str = (
        "DEMONSTRATION ONLY: Synthetic cohort data generated for evaluation and training. "
        "Not representative of actual clinical or community outcomes."
    )


class DemographicBreakdownResponse(BaseModel):
    program: Optional[str] = None
    district: Optional[str] = None
    dimension: str
    total_in_scope: int
    breakdown: Dict[str, Any]
    suppressed_cells_count: int
    k_anonymity_threshold: int = 10
    is_simulated: bool = True
    data_provenance: str = "SYNTHETIC_DEMONSTRATION_DATA"
    provenance_disclaimer: str = (
        "DEMONSTRATION ONLY: Synthetic cohort data generated for evaluation and training. "
        "Not representative of actual clinical or community outcomes."
    )


class SeedCohortCitizen(BaseModel):
    id: str
    name: str
    age: int
    gender: str
    district: str
    program_id: str
    screening_status: Optional[str] = "COMPLETED"  # COMPLETED | DRAFT | CANCELLED
    risk_tier: Optional[str] = "LOW"               # LOW | MODERATE | HIGH | CRITICAL | INSUFFICIENT_DATA
    has_complete_data: bool = True
    observations: Optional[Dict[str, Any]] = None


class SeedCohortRequest(BaseModel):
    program_id: str
    program_name: str
    district: str
    citizens: List[SeedCohortCitizen]


class SeedCohortResponse(BaseModel):
    program_id: str
    seeded_citizens_count: int
    seeded_completed_screenings: int
    seeded_draft_screenings: int
    risk_category_counts: Dict[str, int]
    insufficient_data_count: int = 0
    message: str
    is_simulated: bool = True
    data_provenance: str = "SYNTHETIC_DEMONSTRATION_DATA"

