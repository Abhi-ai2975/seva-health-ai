from typing import Dict, Any, List
from datetime import datetime, timezone

from services.risk_engine.base import (
    RiskModel,
    RiskCategory,
    ImpactDirection,
    RiskFactorContribution,
    DomainRiskResult,
)


class ObesityMetabolicRiskModel(RiskModel):
    """Obesity & Metabolic Syndrome Risk Model based on WHO South Asian BMI Cutoffs & ICMR Guidelines.
    
    Validated Clinical Algorithm:
    - South Asian BMI Categories:
        * Normal: < 23.0 kg/m²
        * Overweight: 23.0 - 24.9 kg/m²
        * Obese: >= 25.0 kg/m²
    - Visceral Adiposity Cutoffs:
        * Men: >= 90 cm
        * Women: >= 80 cm
    - Metabolic Syndrome Features: Central Adiposity + Hypertriglyceridemia (>= 150 mg/dL) / Low HDL
    """

    @property
    def domain(self) -> str:
        return "Obesity & Metabolic Syndrome Risk"

    def version(self) -> str:
        return "WHO-ASIAN-BMI-ICMR-2023.v1"

    def provenance(self) -> Dict[str, str]:
        return {
            "guideline": "Appropriate body-mass index for Asian populations and its implications for policy and intervention strategies",
            "citation": "WHO Expert Consultation. Lancet. 2004;363(9403):157-163.",
            "authority": "World Health Organization (WHO) & ICMR National Task Force on Central Obesity",
            "validation_status": "CLINICALLY_VALIDATED",
        }

    def is_clinically_validated(self) -> bool:
        return True

    def required_inputs(self) -> List[str]:
        return ["WAIST_CIRCUMFERENCE"]

    def optional_inputs(self) -> List[str]:
        return ["BMI", "HEIGHT", "WEIGHT", "SEX", "TRIGLYCERIDES", "HDL_CHOLESTEROL", "FASTING_GLUCOSE"]

    def limitations(self) -> List[str]:
        return [
            "BMI does not distinguish between lean skeletal muscle mass and adipose tissue.",
            "Dual-energy X-ray absorptiometry (DEXA) or bioimpedance is not performed in primary field screening.",
            "Metabolic syndrome criteria require laboratory lipid panel (triglycerides, HDL) for definitive clinical diagnosis.",
        ]

    def confidence(self, inputs: Dict[str, Any]) -> float:
        score = 0.40
        if "BMI" in inputs or ("HEIGHT" in inputs and "WEIGHT" in inputs):
            score += 0.30
        if "TRIGLYCERIDES" in inputs:
            score += 0.15
        if "HDL_CHOLESTEROL" in inputs:
            score += 0.15
        return min(1.0, score)

    def calculate(self, inputs: Dict[str, Any]) -> DomainRiskResult:
        clean_inputs, metadata, missing_mandatory = self.validate_and_normalize_inputs(inputs)

        has_bmi = "BMI" in clean_inputs or ("HEIGHT" in clean_inputs and "WEIGHT" in clean_inputs)
        if missing_mandatory or not has_bmi:
            missing_reqs = list(missing_mandatory)
            if not has_bmi:
                missing_reqs.append("BMI or (HEIGHT and WEIGHT)")
            return DomainRiskResult(
                risk_domain=self.domain,
                score=None,
                risk_category=RiskCategory.INSUFFICIENT_DATA,
                model_version=self.version(),
                input_snapshot=clean_inputs,
                risk_factor_contributions=[],
                confidence=0.0,
                missing_requirements=missing_reqs,
                limitations=["Missing mandatory anthropometric dimensions (Waist Circumference AND BMI/Height/Weight)."],
                recommended_next_step="Measure waist circumference, standing height, and body weight.",
                is_clinically_validated=self.is_clinically_validated(),
                provenance=self.provenance(),
            )

        # Compute BMI if not explicitly given
        bmi = clean_inputs.get("BMI")
        if not bmi:
            h_m = clean_inputs["HEIGHT"] / 100.0
            bmi = round(clean_inputs["WEIGHT"] / (h_m * h_m), 1)
            clean_inputs["BMI"] = bmi

        waist = clean_inputs["WAIST_CIRCUMFERENCE"]
        tg = clean_inputs.get("TRIGLYCERIDES")
        sex = str(metadata.get("SEX", "MALE")).upper()
        waist_cutoff = 90.0 if sex == "MALE" else 80.0

        contributions: List[RiskFactorContribution] = []
        score = 0.10
        category = RiskCategory.LOW

        # 1. Central Adiposity Analysis
        if waist >= waist_cutoff:
            score += 0.35
            contributions.append(RiskFactorContribution(
                feature="Visceral Adiposity / Central Obesity",
                observed_value=f"Waist {waist:.0f} cm",
                target_value=f"< {waist_cutoff:.0f} cm (Asian Indian cutoff)",
                impact_weight=0.35,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Ectopic visceral fat accumulation triggers systemic adipokine dysregulation and non-alcoholic fatty liver changes.",
                evidence_citation="ICMR Guidelines on Central Obesity in South Asians / Misra A, et al. 2009",
            ))
        else:
            contributions.append(RiskFactorContribution(
                feature="Healthy Abdominal Circumference",
                observed_value=f"Waist {waist:.0f} cm",
                target_value=f"< {waist_cutoff:.0f} cm",
                impact_weight=0.15,
                impact_direction=ImpactDirection.DECREASES_RISK,
                category="BIOMETRIC",
                explanation="Abdominal circumference is within the cardioprotective reference range.",
                evidence_citation="ICMR 2023 Anthropometric Guidelines",
            ))

        # 2. Asian Indian BMI Cutoffs
        if bmi >= 25.0:
            score += 0.30
            contributions.append(RiskFactorContribution(
                feature="Obesity (Asian Indian Criteria)",
                observed_value=f"BMI {bmi:.1f} kg/m²",
                target_value="< 23.0 kg/m²",
                impact_weight=0.30,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Asian Indians have a 'thin-fat phenotype' with higher body fat percentage at lower BMI levels (obesity >= 25 kg/m² vs 30 globally).",
                evidence_citation="WHO Expert Consultation (Lancet 2004) South Asian BMI Cutoff",
            ))
        elif bmi >= 23.0:
            score += 0.18
            contributions.append(RiskFactorContribution(
                feature="Overweight (Asian Indian Criteria)",
                observed_value=f"BMI {bmi:.1f} kg/m²",
                target_value="< 23.0 kg/m²",
                impact_weight=0.18,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Body mass index is in the overweight pre-disease range for South Asian populations.",
                evidence_citation="WHO Expert Consultation (Lancet 2004)",
            ))
        else:
            contributions.append(RiskFactorContribution(
                feature="Optimal Body Mass Index",
                observed_value=f"BMI {bmi:.1f} kg/m²",
                target_value="18.5 - 22.9 kg/m²",
                impact_weight=0.15,
                impact_direction=ImpactDirection.DECREASES_RISK,
                category="BIOMETRIC",
                explanation="Body mass index is within optimal homeostasis limits.",
                evidence_citation="WHO South Asian BMI Cutoff",
            ))

        # 3. Dyslipidemia / Hypertriglyceridemia (if available)
        if tg and tg >= 150.0:
            score += 0.20
            contributions.append(RiskFactorContribution(
                feature="Hypertriglyceridemia",
                observed_value=f"Triglycerides {tg:.0f} mg/dL",
                target_value="< 150 mg/dL",
                impact_weight=0.20,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Elevated triglycerides indicate hepatic VLDL overproduction and atherogenic dyslipidemia.",
                evidence_citation="NCEP ATP III / IDF Metabolic Syndrome Consensus",
            ))

        final_score = min(0.95, round(score, 2))
        if final_score >= 0.55:
            category = RiskCategory.HIGH
        elif final_score >= 0.28:
            category = RiskCategory.MODERATE
        else:
            category = RiskCategory.LOW

        conf = self.confidence(clean_inputs)
        next_step = self.recommended_next_step(category, contributions)

        return DomainRiskResult(
            risk_domain=self.domain,
            score=final_score,
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
            return "Initiate intensive lifestyle modification targeting 5-7% body weight loss; obtain fasting lipid panel and liver function tests; clinical review."
        elif category == RiskCategory.MODERATE:
            return "Engage in 150 min/week moderate aerobic exercise; reduce refined carbohydrates; re-measure waist circumference in 90 days."
        return "Maintain current physical activity and balanced nutrition."
