from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from packages.types.enums import RiskTier, TrajectoryTrend
from packages.clinical_models.risk import DomainRiskScores, RiskDriver, ProtectiveFactor
from packages.clinical_models.observations import Observation
from services.wearable.models import WearableProjection
from services.risk_engine.base import (
    RiskModel,
    RiskCategory,
    ImpactDirection,
    DomainRiskResult,
    RiskFactorContribution,
)
from services.risk_engine.models.diabetes import DiabetesRiskModel
from services.risk_engine.models.hypertension import HypertensionRiskModel
from services.risk_engine.models.cardiovascular import CardiovascularRiskModel
from services.risk_engine.models.obesity import ObesityMetabolicRiskModel
from services.risk_engine.models.ckd import CKDRiskModel
from services.risk_engine.models.demonstration_proxy import DemonstrationRiskModel


class ModularRiskAssessment(BaseModel):
    model_name: str = "NCD_MODULAR_RISK_ENGINE"
    model_version: str = "calibrated_ensemble_v1.0.0"
    input_snapshot: Dict[str, Any] = Field(default_factory=dict)
    overall_score: Optional[float] = None
    overall_category: RiskCategory
    confidence: float
    trajectory: TrajectoryTrend
    domain_results: Dict[str, DomainRiskResult]
    top_drivers: List[RiskFactorContribution] = []
    protective_factors: List[RiskFactorContribution] = []
    missing_requirements: List[str] = Field(default_factory=list)
    limitations: List[str] = []
    recommended_next_steps: List[str] = []
    is_clinically_validated: bool = True
    is_demonstration_only: bool = False
    validation_status: str = "CLINICALLY_VALIDATED"
    demonstration_warning: Optional[str] = None
    clinical_summary: str
    clinical_safety_notice: str = (
        "SAFETY NOTICE: Risk estimation is NOT diagnosis. This score indicates statistical "
        "and guideline-based probability. It does NOT independently diagnose illness or prescribe therapy. "
        "High-risk assessments require qualified clinical evaluation."
    )
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))



class NCDRiskEngine:
    """Central orchestration engine for SevaHealth NCD Risk Models.
    
    Coordinates domain-specific models:
    1. Diabetes & Glycemic Risk (ICMR-INDIAB 2023 & IDRS)
    2. Hypertension & Vascular Risk (ACC/AHA 2017 & ICMR)
    3. Cardiovascular 10-Year ASCVD Risk (WHO-SEAR 2019)
    4. Obesity & Metabolic Syndrome (WHO South Asian Cutoffs & ICMR)
    5. Chronic Kidney Disease (KDIGO 2024 & CKD-EPI 2021)
    """

    # Domain weighting for composite risk calculation
    DOMAIN_WEIGHTS = {
        "diabetes": 0.25,
        "hypertension": 0.25,
        "cardiovascular": 0.25,
        "obesity": 0.15,
        "ckd": 0.10,
    }

    def __init__(self):
        self._models: Dict[str, RiskModel] = {
            "diabetes": DiabetesRiskModel(),
            "hypertension": HypertensionRiskModel(),
            "cardiovascular": CardiovascularRiskModel(),
            "obesity": ObesityMetabolicRiskModel(),
            "ckd": CKDRiskModel(),
            "demonstration_proxy": DemonstrationRiskModel(),
        }
        self.engine_name: str = "NCD_MODULAR_RISK_ENGINE"
        self.engine_version: str = "calibrated_ensemble_v1.0.0"

    def set_engine_version(self, version: str):
        """Allows deploying a new test or release version of the composite model (TC-RSK-004)."""
        self.engine_version = version

    def deploy_model(self, key: str, model: RiskModel):
        """Allows registering and deploying a new version of a clinical domain model (TC-RSK-004)."""
        self.register_model(key, model)

    def register_model(self, key: str, model: RiskModel):
        self._models[key] = model

    def get_model(self, key: str) -> Optional[RiskModel]:
        return self._models.get(key)

    def evaluate_model(self, key: str, raw_inputs: Dict[str, Any]) -> DomainRiskResult:
        """Evaluates a single selected clinical model with provided inputs.
        If essential inputs are missing, returns RiskCategory.INSUFFICIENT_DATA
        and identifies missing requirements without inventing a score.
        """
        model = self.get_model(key)
        if not model:
            raise ValueError(f"Clinical model '{key}' not registered in engine")
        return model.calculate(raw_inputs)

    def list_models(self) -> List[Dict[str, Any]]:
        catalog = []
        for key, model in self._models.items():
            catalog.append({
                "key": key,
                "domain": model.domain,
                "version": model.version(),
                "is_clinically_validated": model.is_clinically_validated(),
                "provenance": model.provenance(),
                "required_inputs": model.required_inputs(),
                "optional_inputs": model.optional_inputs(),
                "limitations": model.limitations(),
            })
        return catalog

    def evaluate(
        self,
        raw_inputs: Dict[str, Any],
        wearable_projection: Optional[WearableProjection] = None,
        demonstration_model: bool = False,
        model_key: Optional[str] = None,
    ) -> ModularRiskAssessment:
        """Evaluates registered risk models, normalizes metrics,
        and aggregates explainable contributions and trajectory.
        Supports unvalidated demonstration-only models with explicit labelling.
        """
        is_demo_requested = (
            demonstration_model
            or model_key == "demonstration_proxy"
            or bool(raw_inputs.get("DEMONSTRATION_MODE"))
            or bool(raw_inputs.get("DEMONSTRATION_ONLY"))
            or bool(raw_inputs.get("is_demonstration_only"))
        )

        domain_results: Dict[str, DomainRiskResult] = {}
        all_drivers: List[RiskFactorContribution] = []
        all_protective: List[RiskFactorContribution] = []
        all_limitations: List[str] = []
        all_next_steps: List[str] = []

        if is_demo_requested:
            # Run demonstration proxy model
            models_to_run = {"demonstration_proxy": self._models["demonstration_proxy"]}
        elif model_key and model_key in self._models:
            models_to_run = {model_key: self._models[model_key]}
        else:
            # Standard validated multi-domain clinical evaluation
            models_to_run = {k: v for k, v in self._models.items() if k != "demonstration_proxy"}

        # Run selected models
        for key, model in models_to_run.items():
            result = model.calculate(raw_inputs)
            domain_results[key] = result
            
            # Aggregate limitations
            all_limitations.extend(result.limitations)
            if result.recommended_next_step:
                all_next_steps.append(f"{model.domain}: {result.recommended_next_step}")

            # Collect feature contributions
            for contrib in result.risk_factor_contributions:
                if contrib.impact_direction == ImpactDirection.INCREASES_RISK:
                    all_drivers.append(contrib)
                elif contrib.impact_direction == ImpactDirection.DECREASES_RISK:
                    all_protective.append(contrib)

        # Sort drivers by absolute impact weight descending
        all_drivers.sort(key=lambda d: d.impact_weight, reverse=True)
        all_protective.sort(key=lambda p: abs(p.impact_weight), reverse=True)

        # Calculate composite score across domains with valid scores
        total_weight = 0.0
        weighted_score_sum = 0.0
        conf_sum = 0.0

        for key, res in domain_results.items():
            conf_sum += res.confidence
            if res.score is not None and res.risk_category != RiskCategory.INSUFFICIENT_DATA:
                w = self.DOMAIN_WEIGHTS.get(key, 0.20) if not is_demo_requested else 1.0
                weighted_score_sum += res.score * w
                total_weight += w

        if total_weight > 0:
            composite_score = round(weighted_score_sum / total_weight, 3)
            if composite_score >= 0.55:
                overall_category = RiskCategory.HIGH
            elif composite_score >= 0.25:
                overall_category = RiskCategory.MODERATE
            else:
                overall_category = RiskCategory.LOW
        else:
            composite_score = None
            overall_category = RiskCategory.INSUFFICIENT_DATA

        avg_confidence = round(conf_sum / len(models_to_run), 2) if models_to_run else 0.0

        # Collect missing requirements across domain models
        all_missing_requirements: List[str] = []
        for key, res in domain_results.items():
            if res.risk_category == RiskCategory.INSUFFICIENT_DATA and res.missing_requirements:
                for req in res.missing_requirements:
                    all_missing_requirements.append(f"{res.risk_domain}: {req}")

        # Trajectory determination (modulated by wearable trends if available)
        trajectory = self._determine_trajectory(overall_category, all_drivers, wearable_projection)

        # Build composite clinical summary
        if overall_category == RiskCategory.INSUFFICIENT_DATA:
            clinical_summary = "Insufficient clinical observations available to formulate composite risk estimate."
        else:
            top_str = all_drivers[0].feature if all_drivers else "standard population risk markers"
            clinical_summary = (
                f"Citizen exhibits {overall_category.value} composite NCD risk "
                f"({composite_score*100:.0f}% multi-domain index). "
                f"Top contributing risk factor is {top_str}. "
                f"Longitudinal trajectory is {trajectory.value}."
            )

        if is_demo_requested:
            clinical_summary = f"[DEMONSTRATION ONLY — NOT CLINICALLY VALIDATED] {clinical_summary}"
            all_limitations.insert(
                0,
                "DEMONSTRATION ONLY: This model is an unvalidated demonstration proxy for research and testing. NOT clinically validated."
            )
            demo_warning = (
                "⚠️ DEMONSTRATION ONLY: This model is an unvalidated demonstration proxy for research "
                "and testing. NOT clinically validated for diagnostic or therapeutic decisions."
            )
            safety_notice = (
                "⚠️ DEMONSTRATION ONLY: This assessment was generated by an unvalidated demonstration-only "
                "prototype. It is NOT clinically validated and MUST NOT be used for clinical diagnosis or treatment."
            )
            model_name = "DEMONSTRATION_ONLY_MODEL"
            model_version = "DEMO-PROXY-EXPERIMENTAL-v0.9"
        else:
            demo_warning = None
            safety_notice = (
                "SAFETY NOTICE: Risk estimation is NOT diagnosis. This score indicates statistical "
                "and guideline-based probability. It does NOT independently diagnose illness or prescribe therapy. "
                "High-risk assessments require qualified clinical evaluation."
            )
            model_name = self.engine_name
            model_version = self.engine_version

        return ModularRiskAssessment(
            model_name=model_name,
            model_version=model_version,
            input_snapshot=dict(raw_inputs),
            overall_score=composite_score,
            overall_category=overall_category,
            confidence=avg_confidence,
            trajectory=trajectory,
            domain_results=domain_results,
            top_drivers=all_drivers,
            protective_factors=all_protective,
            missing_requirements=all_missing_requirements,
            limitations=list(dict.fromkeys(all_limitations)),  # deduplicate
            recommended_next_steps=all_next_steps,
            clinical_summary=clinical_summary,
            is_clinically_validated=not is_demo_requested,
            is_demonstration_only=is_demo_requested,
            validation_status="DEMONSTRATION_ONLY" if is_demo_requested else "CLINICALLY_VALIDATED",
            demonstration_warning=demo_warning,
            clinical_safety_notice=safety_notice,
        )

    def _determine_trajectory(
        self,
        overall_category: RiskCategory,
        drivers: List[RiskFactorContribution],
        wearable_projection: Optional[WearableProjection] = None,
    ) -> TrajectoryTrend:
        """Determines longitudinal risk trajectory with wearable modulation."""
        base_trend = TrajectoryTrend.STABLE

        if overall_category == RiskCategory.HIGH:
            base_trend = TrajectoryTrend.DETERIORATING
        elif overall_category == RiskCategory.LOW:
            base_trend = TrajectoryTrend.STABLE

        # Wearable modulation
        if wearable_projection:
            # If wearable indicates active lifestyle / high adherence
            if wearable_projection.step_target_adherence_pct >= 0.80 and wearable_projection.avg_daily_steps >= 8000:
                if base_trend == TrajectoryTrend.DETERIORATING:
                    base_trend = TrajectoryTrend.STABLE
                elif base_trend == TrajectoryTrend.STABLE:
                    base_trend = TrajectoryTrend.IMPROVING
            # If resting heart rate is markedly elevated or adherence very poor
            elif wearable_projection.avg_resting_heart_rate and wearable_projection.avg_resting_heart_rate > 90:
                base_trend = TrajectoryTrend.DETERIORATING

        return base_trend

    def evaluate_observations(
        self,
        observations: List[Observation],
        idrs_score: Optional[int] = 40,
        age: Optional[int] = 45,
        gender: str = "MALE",
        smoker: bool = False,
        wearable_projection: Optional[WearableProjection] = None,
    ) -> Tuple[DomainRiskScores, Optional[float], RiskTier, TrajectoryTrend, List[RiskDriver], List[ProtectiveFactor]]:
        """Bridge adapter converting raw observations into the legacy tuple format
        for backward compatibility with existing platform components.
        """
        raw_inputs: Dict[str, Any] = {
            "SEX": gender,
            "SMOKING": smoker,
        }
        if age is not None:
            raw_inputs["AGE"] = age
        if idrs_score is not None:
            raw_inputs["IDRS_SCORE"] = idrs_score
        for o in observations:
            raw_inputs[o.code] = o.value

        mod_assessment = self.evaluate(raw_inputs, wearable_projection)

        # Map domain scores to legacy DomainRiskScores
        doms = mod_assessment.domain_results
        d_scores = DomainRiskScores(
            diabetes_risk=doms["diabetes"].score,
            hypertension_risk=doms["hypertension"].score,
            cardiovascular_risk=doms["cardiovascular"].score,
            metabolic_syndrome_risk=doms["obesity"].score,
            ckd_risk=doms["ckd"].score,
            fatty_liver_risk=min(1.0, (doms["obesity"].score * 1.1)) if doms["obesity"].score is not None else None,
        )

        overall_score = mod_assessment.overall_score

        # Map to RiskTier without inventing scores
        if mod_assessment.overall_category == RiskCategory.INSUFFICIENT_DATA or overall_score is None:
            overall_tier = RiskTier.INSUFFICIENT_DATA
        elif mod_assessment.overall_category == RiskCategory.HIGH:
            overall_tier = RiskTier.CRITICAL if overall_score >= 0.75 else RiskTier.HIGH
        elif mod_assessment.overall_category == RiskCategory.MODERATE:
            overall_tier = RiskTier.MODERATE
        else:
            overall_tier = RiskTier.LOW

        # Convert contributions to legacy RiskDriver and ProtectiveFactor
        legacy_drivers = [
            RiskDriver(
                feature_name=d.feature,
                observed_value=d.observed_value,
                target_value=d.target_value,
                impact_weight=d.impact_weight,
                category=d.category,
                evidence_citation=d.evidence_citation,
            )
            for d in mod_assessment.top_drivers[:5]
        ]

        legacy_protective = [
            ProtectiveFactor(
                feature_name=p.feature,
                observed_value=p.observed_value,
                impact_weight=p.impact_weight,
                category=p.category,
            )
            for p in mod_assessment.protective_factors[:5]
        ]

        return (
            d_scores,
            overall_score,
            overall_tier,
            mod_assessment.trajectory,
            legacy_drivers,
            legacy_protective,
        )


# Global singleton instance
ncd_risk_engine = NCDRiskEngine()
