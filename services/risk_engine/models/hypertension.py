from typing import Dict, Any, List
from datetime import datetime, timezone

from services.risk_engine.base import (
    RiskModel,
    RiskCategory,
    ImpactDirection,
    RiskFactorContribution,
    DomainRiskResult,
)


class HypertensionRiskModel(RiskModel):
    """Hypertension & Vascular Risk Model based on 2017 ACC/AHA Guidelines & ICMR Protocol.
    
    Validated Clinical Algorithm:
    - Normal: SBP < 120 mmHg AND DBP < 80 mmHg
    - Elevated: SBP 120-129 mmHg AND DBP < 80 mmHg
    - Stage 1 HTN: SBP 130-139 mmHg OR DBP 80-89 mmHg
    - Stage 2 HTN: SBP >= 140 mmHg OR DBP >= 90 mmHg (Critical >= 160/100)
    """

    @property
    def domain(self) -> str:
        return "Hypertension & Vascular Risk"

    def version(self) -> str:
        return "ACC-AHA-2017-ICMR.v1"

    def provenance(self) -> Dict[str, str]:
        return {
            "guideline": "2017 ACC/AHA/AAPA/ABC/ACPM/AGS/APhA/ASH/ASPC/NMA/PCNA High Blood Pressure Clinical Practice Guideline",
            "citation": "Whelton PK, et al. Hypertension. 2018;71(6):e13-e115.",
            "authority": "American College of Cardiology (ACC) & American Heart Association (AHA)",
            "validation_status": "CLINICALLY_VALIDATED",
        }

    def is_clinically_validated(self) -> bool:
        return True

    def required_inputs(self) -> List[str]:
        return ["SYSTOLIC_BP", "DIASTOLIC_BP"]

    def optional_inputs(self) -> List[str]:
        return ["HEART_RATE", "AGE", "SMOKING", "SODIUM_INTAKE", "STRESS_LEVEL"]

    def limitations(self) -> List[str]:
        return [
            "Single automated or manual blood pressure measurement cannot confirm chronic clinical hypertension (White-coat effect).",
            "Requires serial confirmation across at least two separate seated visits or 24h ambulatory monitoring.",
            "Cuff size mismatch (e.g. standard adult cuff on arm circumference > 35cm) can artificially elevate systolic readings.",
        ]

    def confidence(self, inputs: Dict[str, Any]) -> float:
        # Full confidence if both SBP and DBP provided; 0 if missing either
        if "SYSTOLIC_BP" in inputs and "DIASTOLIC_BP" in inputs:
            return 0.95
        return 0.0

    def calculate(self, inputs: Dict[str, Any]) -> DomainRiskResult:
        clean_inputs, metadata, missing_mandatory = self.validate_and_normalize_inputs(inputs)

        if missing_mandatory:
            return DomainRiskResult(
                risk_domain=self.domain,
                score=None,
                risk_category=RiskCategory.INSUFFICIENT_DATA,
                model_version=self.version(),
                input_snapshot=clean_inputs,
                risk_factor_contributions=[],
                confidence=0.0,
                missing_requirements=list(missing_mandatory),
                limitations=[f"Missing mandatory vascular pressure measurements: {', '.join(missing_mandatory)}."],
                recommended_next_step="Perform seated bilateral blood pressure screening measurement.",
                is_clinically_validated=self.is_clinically_validated(),
                provenance=self.provenance(),
            )

        sbp = clean_inputs["SYSTOLIC_BP"]
        dbp = clean_inputs["DIASTOLIC_BP"]

        contributions: List[RiskFactorContribution] = []
        score = 0.10
        category = RiskCategory.LOW

        # Stage 2 Hypertension / Critical
        if sbp >= 160.0 or dbp >= 100.0:
            score = 0.92
            category = RiskCategory.HIGH
            contributions.append(RiskFactorContribution(
                feature="Stage 2 Severe Hypertension",
                observed_value=f"{sbp:.0f}/{dbp:.0f} mmHg",
                target_value="< 120/80 mmHg",
                impact_weight=0.40,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Blood pressure exceeds 160/100 mmHg, creating acute endothelial shear stress and high cerebrovascular event risk.",
                evidence_citation="2017 ACC/AHA High Blood Pressure Guidelines (Stage 2 Severe)",
            ))
        elif sbp >= 140.0 or dbp >= 90.0:
            score = 0.75
            category = RiskCategory.HIGH
            contributions.append(RiskFactorContribution(
                feature="Stage 2 Clinical Hypertension",
                observed_value=f"{sbp:.0f}/{dbp:.0f} mmHg",
                target_value="< 120/80 mmHg",
                impact_weight=0.30,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Vascular pressures meet clinical criteria for Stage 2 hypertension, requiring clinical review and lifestyle modification.",
                evidence_citation="ACC/AHA 2017 High Blood Pressure Criteria",
            ))
        elif sbp >= 130.0 or dbp >= 80.0:
            score = 0.50
            category = RiskCategory.MODERATE
            contributions.append(RiskFactorContribution(
                feature="Stage 1 Pre-Hypertensive Strain",
                observed_value=f"{sbp:.0f}/{dbp:.0f} mmHg",
                target_value="< 120/80 mmHg",
                impact_weight=0.20,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Mild to moderate vascular resistance detected. Highly responsive to dietary sodium reduction and aerobic exercise.",
                evidence_citation="ACC/AHA 2017 Stage 1 Hypertension Guidelines",
            ))
        elif sbp >= 120.0 and dbp < 80.0:
            score = 0.30
            category = RiskCategory.MODERATE
            contributions.append(RiskFactorContribution(
                feature="Elevated Systolic Pressure",
                observed_value=f"{sbp:.0f}/{dbp:.0f} mmHg",
                target_value="< 120/80 mmHg",
                impact_weight=0.12,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Systolic blood pressure is elevated above optimal homeostasis.",
                evidence_citation="ACC/AHA 2017 Elevated Blood Pressure Category",
            ))
        else:
            score = 0.10
            category = RiskCategory.LOW
            contributions.append(RiskFactorContribution(
                feature="Optimal Blood Pressure",
                observed_value=f"{sbp:.0f}/{dbp:.0f} mmHg",
                target_value="< 120/80 mmHg",
                impact_weight=0.18,
                impact_direction=ImpactDirection.DECREASES_RISK,
                category="BIOMETRIC",
                explanation="Hemodynamic readings are within healthy physiological limits.",
                evidence_citation="ACC/AHA 2017 Normal Blood Pressure Consensus",
            ))

        # Pulse pressure analysis
        pulse_pressure = sbp - dbp
        if pulse_pressure >= 60.0:
            contributions.append(RiskFactorContribution(
                feature="Widened Pulse Pressure",
                observed_value=f"{pulse_pressure:.0f} mmHg (SBP-DBP)",
                target_value="< 50 mmHg",
                impact_weight=0.10,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Pulse pressure >= 60 mmHg reflects central aortic arterial stiffness.",
                evidence_citation="Franklin SS, et al. Circulation 1999 (Arterial Compliance)",
            ))

        conf = self.confidence(clean_inputs)
        next_step = self.recommended_next_step(category, contributions)

        return DomainRiskResult(
            risk_domain=self.domain,
            score=round(score, 2),
            risk_category=category,
            model_version=self.version(),
            input_snapshot=clean_inputs,
            risk_factor_contributions=contributions,
            confidence=round(conf, 2),
            limitations=self.limitations(),
            recommended_next_step=next_step,
            is_clinically_validated=self.is_clinically_validated(),
            provenance=self.provenance(),
        )

    def explain(self, inputs: Dict[str, Any], result: DomainRiskResult) -> List[RiskFactorContribution]:
        return result.risk_factor_contributions

    def recommended_next_step(self, category: RiskCategory, contributions: List[RiskFactorContribution]) -> str:
        if category == RiskCategory.HIGH:
            return "Refer for physician evaluation; repeat seated BP measurement after 1 week; restrict dietary sodium to < 2000 mg/day."
        elif category == RiskCategory.MODERATE:
            return "Initiate lifestyle medicine plan: DASH dietary pattern (< 2g sodium/day), daily brisk walking, and stress management."
        return "Re-screen blood pressure annually as part of routine preventive checkups."
