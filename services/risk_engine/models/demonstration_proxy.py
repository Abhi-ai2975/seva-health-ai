from typing import Dict, Any, List
from datetime import datetime, timezone

from services.risk_engine.base import (
    RiskModel,
    RiskCategory,
    ImpactDirection,
    RiskFactorContribution,
    DomainRiskResult,
)


class DemonstrationRiskModel(RiskModel):
    """Demonstration-Only Multi-Omic & Phenotypic Risk Model.
    
    PURPOSE:
    This model serves as an explicit, unvalidated demonstration proxy designed to
    verify model labeling, system safety warnings, regulatory compliance boundaries,
    and UI/report disclaimer enforcement (TC-RSK-003).

    SAFETY DECLARATION:
    - NOT clinically validated.
    - NOT peer reviewed.
    - NOT approved for diagnostic, prognostic, or therapeutic decisions.
    """

    DEMONSTRATION_DISCLAIMER: str = (
        "⚠️ DEMONSTRATION ONLY: This model is an unvalidated demonstration proxy for research "
        "and testing. NOT clinically validated for diagnostic or therapeutic decisions."
    )

    @property
    def domain(self) -> str:
        return "Demonstration Multi-Omic & Phenotypic Risk Proxy"

    def version(self) -> str:
        return "DEMO-PROXY-EXPERIMENTAL-v0.9"

    def provenance(self) -> Dict[str, str]:
        return {
            "guideline": "Experimental Synthetic Multi-Omic Proxy (DEMONSTRATION ONLY)",
            "citation": "Unvalidated Prototype Algorithm - For Research, Interface Testing and Demonstration Only",
            "authority": "SevaHealth Experimental AI Lab (Non-Clinical)",
            "validation_status": "DEMONSTRATION_ONLY",
            "regulatory_status": "NOT_CLINICALLY_VALIDATED",
            "warning": self.DEMONSTRATION_DISCLAIMER,
        }

    def is_clinically_validated(self) -> bool:
        return False

    def required_inputs(self) -> List[str]:
        return []

    def optional_inputs(self) -> List[str]:
        return [
            "AGE",
            "SEX",
            "SYSTOLIC_BP",
            "FASTING_GLUCOSE",
            "BMI",
            "TOTAL_CHOLESTEROL",
            "SMOKING",
        ]

    def limitations(self) -> List[str]:
        return [
            "DEMONSTRATION ONLY: This model is an unvalidated demonstration proxy for research and testing.",
            "NOT clinically validated: Must NOT be used for medical diagnosis, clinical staging, or therapeutic interventions.",
            "Lacks clinical peer review and regulatory clearance under ABDM/CDSCO/FDA guidelines.",
            "All predictions are experimental approximations for demonstration workflow verification only.",
        ]

    def confidence(self, inputs: Dict[str, Any]) -> float:
        # Deliberately lower confidence because this is an unvalidated demonstration model
        return 0.40

    def calculate(self, inputs: Dict[str, Any]) -> DomainRiskResult:
        clean_inputs, metadata, _ = self.validate_and_normalize_inputs(inputs)

        contributions: List[RiskFactorContribution] = []
        limits: List[str] = list(self.limitations())

        age = clean_inputs.get("AGE", 45.0)
        sbp = clean_inputs.get("SYSTOLIC_BP", 120.0)
        fbg = clean_inputs.get("FASTING_GLUCOSE", 95.0)
        bmi = clean_inputs.get("BMI", 22.0)

        # Calculate demonstration proxy score
        score_components = []
        if sbp >= 140.0:
            score_components.append(0.35)
            contributions.append(RiskFactorContribution(
                feature="Experimental Vascular Proxy (DEMO ONLY)",
                observed_value=f"SBP {sbp:.0f} mmHg",
                target_value="< 120 mmHg",
                impact_weight=0.25,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Demonstration proxy calculation for vascular strain. NOT CLINICALLY VALIDATED.",
                evidence_citation="Prototype Heuristic (DEMO ONLY)",
            ))
        elif sbp >= 130.0:
            score_components.append(0.20)
            contributions.append(RiskFactorContribution(
                feature="Experimental Mild Pressure Proxy (DEMO ONLY)",
                observed_value=f"SBP {sbp:.0f} mmHg",
                target_value="< 120 mmHg",
                impact_weight=0.15,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Demonstration proxy calculation. NOT CLINICALLY VALIDATED.",
                evidence_citation="Prototype Heuristic (DEMO ONLY)",
            ))

        if fbg >= 126.0:
            score_components.append(0.40)
            contributions.append(RiskFactorContribution(
                feature="Experimental Glycemic Proxy (DEMO ONLY)",
                observed_value=f"FBG {fbg:.0f} mg/dL",
                target_value="< 100 mg/dL",
                impact_weight=0.30,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Demonstration proxy calculation for glycemic variance. NOT CLINICALLY VALIDATED.",
                evidence_citation="Prototype Heuristic (DEMO ONLY)",
            ))

        if bmi >= 25.0:
            score_components.append(0.25)
            contributions.append(RiskFactorContribution(
                feature="Experimental Anthropometric Proxy (DEMO ONLY)",
                observed_value=f"BMI {bmi:.1f} kg/m²",
                target_value="< 23.0 kg/m²",
                impact_weight=0.18,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Demonstration proxy calculation. NOT CLINICALLY VALIDATED.",
                evidence_citation="Prototype Heuristic (DEMO ONLY)",
            ))

        raw_score = sum(score_components) if score_components else 0.12
        score = min(0.95, round(raw_score, 2))

        if score >= 0.55:
            category = RiskCategory.HIGH
        elif score >= 0.25:
            category = RiskCategory.MODERATE
        else:
            category = RiskCategory.LOW

        if not contributions:
            contributions.append(RiskFactorContribution(
                feature="Experimental Baseline Indicators (DEMO ONLY)",
                observed_value="Baseline inputs within standard range",
                target_value="Standard range",
                impact_weight=0.10,
                impact_direction=ImpactDirection.DECREASES_RISK,
                category="DEMOGRAPHIC",
                explanation="Demonstration proxy calculation. NOT CLINICALLY VALIDATED.",
                evidence_citation="Prototype Heuristic (DEMO ONLY)",
            ))

        prov = dict(self.provenance())
        prov["validation_status"] = "DEMONSTRATION_ONLY"
        prov["clinical_disclaimer"] = self.DEMONSTRATION_DISCLAIMER

        return DomainRiskResult(
            risk_domain=self.domain,
            score=score,
            risk_category=category,
            model_version=self.version(),
            input_snapshot=clean_inputs,
            risk_factor_contributions=contributions,
            confidence=self.confidence(clean_inputs),
            limitations=limits,
            recommended_next_step=self.recommended_next_step(category, contributions),
            is_clinically_validated=False,
            provenance=prov,
            clinical_safety_notice=self.DEMONSTRATION_DISCLAIMER,
        )

    def explain(self, inputs: Dict[str, Any], result: DomainRiskResult) -> List[RiskFactorContribution]:
        return result.risk_factor_contributions

    def recommended_next_step(self, category: RiskCategory, contributions: List[RiskFactorContribution]) -> str:
        return (
            "DEMONSTRATION OUTPUT ONLY: Do NOT use for medical advice. "
            "Patient requires evaluation with validated clinical guidelines and licensed medical staff."
        )
