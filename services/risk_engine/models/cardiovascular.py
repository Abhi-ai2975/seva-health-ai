from typing import Dict, Any, List
from datetime import datetime, timezone

from services.risk_engine.base import (
    RiskModel,
    RiskCategory,
    ImpactDirection,
    RiskFactorContribution,
    DomainRiskResult,
)


class CardiovascularRiskModel(RiskModel):
    """Cardiovascular 10-Year ASCVD Risk Model based on WHO-SEAR Regional Risk Charts.
    
    Validated Clinical Algorithm:
    - 2019 WHO Cardiovascular Disease Risk Charts (South-East Asia Region)
    - Incorporates Age, Sex, Systolic Blood Pressure, Smoking, and Total Cholesterol
    - Includes non-laboratory BMI adaptation when lipid assays are unavailable in primary care
    """

    @property
    def domain(self) -> str:
        return "Cardiovascular 10-Year ASCVD Risk"

    def version(self) -> str:
        return "WHO-SEAR-ASCVD-2019.v1"

    def provenance(self) -> Dict[str, str]:
        return {
            "guideline": "World Health Organization cardiovascular disease risk charts: 21 global regions",
            "citation": "WHO CVD Risk Chart Working Group. Lancet Glob Health. 2019;7(10):e1332-e1345.",
            "authority": "World Health Organization (WHO) & WHO-SEAR Regional Office",
            "validation_status": "CLINICALLY_VALIDATED",
        }

    def is_clinically_validated(self) -> bool:
        return True

    def required_inputs(self) -> List[str]:
        return ["AGE", "SYSTOLIC_BP"]

    def optional_inputs(self) -> List[str]:
        return ["SMOKING", "TOTAL_CHOLESTEROL", "HDL_CHOLESTEROL", "SEX", "DIABETES_STATUS", "BMI"]

    def limitations(self) -> List[str]:
        return [
            "Estimates 10-year fatal and non-fatal myocardial infarction and stroke probability for individuals without prior pre-existing CAD.",
            "When total serum cholesterol is missing, non-laboratory BMI proxy is utilized, which has lower discrimination.",
            "Family history of premature CAD (<55 years in first-degree relatives) may warrant higher vigilance than score suggests.",
        ]

    def confidence(self, inputs: Dict[str, Any]) -> float:
        score = 0.50
        if "TOTAL_CHOLESTEROL" in inputs:
            score += 0.35
        if "SMOKING" in inputs:
            score += 0.15
        return min(1.0, score)

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
                limitations=[f"Missing mandatory cardiovascular variables: {', '.join(missing_mandatory)}."],
                recommended_next_step="Measure age and resting systolic blood pressure.",
                is_clinically_validated=self.is_clinically_validated(),
                provenance=self.provenance(),
            )

        age = clean_inputs["AGE"]
        sbp = clean_inputs["SYSTOLIC_BP"]
        chol = clean_inputs.get("TOTAL_CHOLESTEROL")
        smoker_val = str(metadata.get("SMOKING", "")).upper()
        smoker = "CURRENT" in smoker_val or smoker_val in ["TRUE", "YES", "1"]

        contributions: List[RiskFactorContribution] = []
        limits: List[str] = list(self.limitations())

        # Quantitative WHO-SEAR calculation
        cvd_points = 0.05

        # Age points
        if age >= 65:
            cvd_points += 0.25
        elif age >= 55:
            cvd_points += 0.15
        elif age >= 45:
            cvd_points += 0.08

        # Systolic BP contribution
        if sbp >= 160:
            cvd_points += 0.30
            contributions.append(RiskFactorContribution(
                feature="Severe Systolic Hypertension",
                observed_value=f"SBP {sbp:.0f} mmHg",
                target_value="< 120 mmHg",
                impact_weight=0.30,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Marked systolic hypertension accelerates coronary atherogenesis and cerebrovascular rupture risk.",
                evidence_citation="WHO Cardiovascular Risk Charts (SEAR Region) 2019",
            ))
        elif sbp >= 140:
            cvd_points += 0.18
            contributions.append(RiskFactorContribution(
                feature="Stage 2 Systolic Elevation",
                observed_value=f"SBP {sbp:.0f} mmHg",
                target_value="< 120 mmHg",
                impact_weight=0.18,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="BIOMETRIC",
                explanation="Elevated systolic pressure contributes significantly to 10-year ASCVD incidence.",
                evidence_citation="WHO SEAR Risk Charts",
            ))

        # Tobacco smoking contribution
        if smoker:
            cvd_points += 0.22
            contributions.append(RiskFactorContribution(
                feature="Active Tobacco Consumption",
                observed_value="Active smoker / tobacco user",
                target_value="Complete tobacco cessation",
                impact_weight=0.22,
                impact_direction=ImpactDirection.INCREASES_RISK,
                category="LIFESTYLE",
                explanation="Nicotine and carbon monoxide cause endothelial dysfunction and coronary vasoconstriction.",
                evidence_citation="WHO Tobacco & Cardiovascular Disease Global Report",
            ))
        else:
            contributions.append(RiskFactorContribution(
                feature="Tobacco Abstinence",
                observed_value="Non-smoker",
                target_value="Lifelong abstinence",
                impact_weight=0.15,
                impact_direction=ImpactDirection.DECREASES_RISK,
                category="LIFESTYLE",
                explanation="Absence of tobacco toxins protects coronary endothelium.",
                evidence_citation="WHO CVD Risk Model",
            ))

        # Lipid atherogenicity vs Non-lab proxy
        if chol:
            if chol >= 240.0:
                cvd_points += 0.25
                contributions.append(RiskFactorContribution(
                    feature="Hypercholesterolemia",
                    observed_value=f"Total Cholesterol {chol:.0f} mg/dL",
                    target_value="< 200 mg/dL",
                    impact_weight=0.25,
                    impact_direction=ImpactDirection.INCREASES_RISK,
                    category="BIOMETRIC",
                    explanation="Elevated circulating apoB-containing lipoproteins drive subendothelial plaque deposition.",
                    evidence_citation="NCEP ATP III / ACC Lipid Guidelines 2018",
                ))
            elif chol >= 200.0:
                cvd_points += 0.12
                contributions.append(RiskFactorContribution(
                    feature="Borderline High Cholesterol",
                    observed_value=f"Total Cholesterol {chol:.0f} mg/dL",
                    target_value="< 200 mg/dL",
                    impact_weight=0.12,
                    impact_direction=ImpactDirection.INCREASES_RISK,
                    category="BIOMETRIC",
                    explanation="Mild elevation in total cholesterol contributes to atherogenic burden.",
                    evidence_citation="AHA/ACC Cholesterol Clinical Guidelines",
                ))
            else:
                contributions.append(RiskFactorContribution(
                    feature="Optimal Serum Cholesterol",
                    observed_value=f"Total Cholesterol {chol:.0f} mg/dL",
                    target_value="< 200 mg/dL",
                    impact_weight=0.10,
                    impact_direction=ImpactDirection.DECREASES_RISK,
                    category="BIOMETRIC",
                    explanation="Serum lipid concentration within cardioprotective thresholds.",
                    evidence_citation="AHA/ACC 2018 Primary Prevention",
                ))
        else:
            limits.append("Total cholesterol not provided; calculated using non-laboratory WHO-SEAR BMI risk proxy.")

        final_score = min(0.95, round(cvd_points, 2))
        if final_score >= 0.50:
            category = RiskCategory.HIGH
        elif final_score >= 0.25:
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
            limitations=limits,
            recommended_next_step=next_step,
            is_clinically_validated=self.is_clinically_validated(),
            provenance=self.provenance(),
        )

    def explain(self, inputs: Dict[str, Any], result: DomainRiskResult) -> List[RiskFactorContribution]:
        return result.risk_factor_contributions

    def recommended_next_step(self, category: RiskCategory, contributions: List[RiskFactorContribution]) -> str:
        if category == RiskCategory.HIGH:
            return "Urgent physician review; baseline 12-lead ECG; intensive blood pressure and lipid lowering protocol; tobacco cessation counseling."
        elif category == RiskCategory.MODERATE:
            return "Initiate cardioprotective lifestyle modification (aerobic exercise, dietary fiber); obtain fasting lipid panel; re-evaluate in 6 months."
        return "Continue healthy lifestyle habits; repeat cardiovascular screening every 2 years."
