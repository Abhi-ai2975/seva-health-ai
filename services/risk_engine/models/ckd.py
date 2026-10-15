from typing import Dict, Any, List
from datetime import datetime, timezone

from services.risk_engine.base import (
    RiskModel,
    RiskCategory,
    ImpactDirection,
    RiskFactorContribution,
    DomainRiskResult,
)


class CKDRiskModel(RiskModel):
    """Chronic Kidney Disease (CKD) Risk Model based on KDIGO 2024 & CKD-EPI 2021 Equation.
    
    Validated Clinical Algorithm:
    - KDIGO 2024 GFR Categories:
        * G1: Normal or high (eGFR >= 90 mL/min/1.73m²)
        * G2: Mildly decreased (eGFR 60-89 mL/min/1.73m²)
        * G3a: Mildly to moderately decreased (eGFR 45-59 mL/min/1.73m²)
        * G3b: Moderately to severely decreased (eGFR 30-44 mL/min/1.73m²)
        * G4-G5: Severely decreased / Kidney failure (eGFR < 30 mL/min/1.73m²)
    - Where laboratory serum creatinine/eGFR is unavailable in community screening,
      a heuristic microvascular progression proxy is offered, clearly marked as
      DEMONSTRATION ONLY (not clinically validated).
    """

    @property
    def domain(self) -> str:
        return "Chronic Kidney Disease (CKD) Risk"

    def version(self) -> str:
        return "KDIGO-CKD-EPI-2024.v1"

    def provenance(self) -> Dict[str, str]:
        return {
            "guideline": "KDIGO 2024 Clinical Practice Guideline for the Evaluation and Management of Chronic Kidney Disease",
            "citation": "Inker LA, et al. New Creatinine- and Cystatin C-Based Equations to Estimate GFR without Race. N Engl J Med. 2021;385:1737-1749.",
            "authority": "Kidney Disease: Improving Global Outcomes (KDIGO)",
            "validation_status": "CLINICALLY_VALIDATED_WHEN_EGFR_OR_CREATININE_PRESENT",
        }

    def is_clinically_validated(self) -> bool:
        # Evaluated dynamically in calculate based on whether validated biomarker was present
        return True

    def required_inputs(self) -> List[str]:
        # Accepts either direct eGFR, serum creatinine, or co-occurring vascular comorbidities
        return []

    def optional_inputs(self) -> List[str]:
        return [
            "EGFR",
            "SERUM_CREATININE",
            "AGE",
            "SEX",
            "SYSTOLIC_BP",
            "FASTING_GLUCOSE",
            "HBA1C",
            "URINE_ACR",
        ]

    def limitations(self) -> List[str]:
        return [
            "Definitive CKD staging requires serial persistence of reduced GFR for >= 3 months or documented structural kidney damage.",
            "Urine Albumin-to-Creatinine Ratio (uACR) is essential for complete KDIGO 2D heat-map staging but rarely collected at initial field screening.",
            "Non-laboratory risk proxies based on diabetes and hypertension are DEMONSTRATION ONLY and must not be used for clinical diagnosis.",
        ]

    def confidence(self, inputs: Dict[str, Any]) -> float:
        if "EGFR" in inputs or "SERUM_CREATININE" in inputs:
            if "URINE_ACR" in inputs:
                return 0.95
            return 0.85
        if "SYSTOLIC_BP" in inputs or "FASTING_GLUCOSE" in inputs:
            return 0.35  # Low confidence heuristic proxy
        return 0.0

    def calculate(self, inputs: Dict[str, Any]) -> DomainRiskResult:
        clean_inputs, metadata, _ = self.validate_and_normalize_inputs(inputs)

        has_renal_biomarker = "EGFR" in clean_inputs or "SERUM_CREATININE" in clean_inputs
        has_comorbidity = any(k in clean_inputs for k in ["SYSTOLIC_BP", "HBA1C", "FASTING_GLUCOSE"])

        if not has_renal_biomarker and not has_comorbidity:
            return DomainRiskResult(
                risk_domain=self.domain,
                score=None,
                risk_category=RiskCategory.INSUFFICIENT_DATA,
                model_version=self.version(),
                input_snapshot=clean_inputs,
                risk_factor_contributions=[],
                confidence=0.0,
                missing_requirements=["EGFR or SERUM_CREATININE or Cardiovascular Comorbidities (SYSTOLIC_BP, HBA1C, FASTING_GLUCOSE)"],
                limitations=["No renal filtration markers (eGFR/serum creatinine) or cardiovascular comorbidities provided."],
                recommended_next_step="Order basic metabolic panel with serum creatinine and estimated GFR.",
                is_clinically_validated=False,
                provenance=self.provenance(),
            )

        contributions: List[RiskFactorContribution] = []
        limits: List[str] = list(self.limitations())

        # PATHWAY A: Validated Biomarker Algorithm (eGFR or Serum Creatinine)
        if has_renal_biomarker:
            egfr = clean_inputs.get("EGFR")
            if not egfr and "SERUM_CREATININE" in clean_inputs:
                scr = clean_inputs["SERUM_CREATININE"]
                age = float(clean_inputs.get("AGE", 50))
                sex = str(metadata.get("SEX", "MALE")).upper()
                is_female = (sex == "FEMALE")
                k = 0.7 if is_female else 0.9
                alpha = -0.241 if is_female else -0.302
                mult = 1.012 if is_female else 1.0

                scr_k = scr / k
                min_val = min(scr_k, 1.0) ** alpha
                max_val = max(scr_k, 1.0) ** (-1.200)
                age_factor = 0.9938 ** age
                egfr = round(142.0 * min_val * max_val * age_factor * mult, 1)
                clean_inputs["EGFR"] = egfr

            if egfr < 60.0:
                score = 0.85
                category = RiskCategory.HIGH
                contributions.append(RiskFactorContribution(
                    feature="Reduced Glomerular Filtration Rate (G3-G5)",
                    observed_value=f"eGFR {egfr:.0f} mL/min/1.73m²",
                    target_value=">= 90 mL/min/1.73m²",
                    impact_weight=0.45,
                    impact_direction=ImpactDirection.INCREASES_RISK,
                    category="BIOMETRIC",
                    explanation="eGFR below 60 mL/min/1.73m² indicates clinically significant loss of nephron functional mass.",
                    evidence_citation="KDIGO 2024 Clinical Practice Guideline for CKD Evaluation",
                ))
            elif egfr < 90.0:
                score = 0.40
                category = RiskCategory.MODERATE
                contributions.append(RiskFactorContribution(
                    feature="Mildly Decreased GFR (G2)",
                    observed_value=f"eGFR {egfr:.0f} mL/min/1.73m²",
                    target_value=">= 90 mL/min/1.73m²",
                    impact_weight=0.20,
                    impact_direction=ImpactDirection.INCREASES_RISK,
                    category="BIOMETRIC",
                    explanation="Mild reduction in filtration rate; requires monitoring of blood pressure and urine albumin.",
                    evidence_citation="KDIGO 2024 Guidelines",
                ))
            else:
                score = 0.08
                category = RiskCategory.LOW
                contributions.append(RiskFactorContribution(
                    feature="Optimal Glomerular Filtration (G1)",
                    observed_value=f"eGFR {egfr:.0f} mL/min/1.73m²",
                    target_value=">= 90 mL/min/1.73m²",
                    impact_weight=0.20,
                    impact_direction=ImpactDirection.DECREASES_RISK,
                    category="BIOMETRIC",
                    explanation="Kidney filtration capacity is well preserved within healthy parameters.",
                    evidence_citation="KDIGO 2024 Criteria",
                ))
            is_valid = True

        # PATHWAY B: Heuristic / Demonstration Proxy (Co-occurring HTN & Glycemia)
        else:
            is_valid = False
            limits.append("DEMONSTRATION ONLY: No direct creatinine/eGFR provided; risk estimated via microvascular proxy.")
            score = 0.15
            category = RiskCategory.LOW

            sbp = clean_inputs.get("SYSTOLIC_BP", 120.0)
            fbg = clean_inputs.get("FASTING_GLUCOSE", 95.0)
            hba1c = clean_inputs.get("HBA1C", 5.4)

            if sbp >= 140.0 or hba1c >= 6.5 or fbg >= 126.0:
                score = 0.50
                category = RiskCategory.MODERATE
                contributions.append(RiskFactorContribution(
                    feature="Microvascular Comorbidity Strain (DEMO ONLY)",
                    observed_value=f"SBP {sbp:.0f} mmHg, HbA1c {hba1c:.1f}%",
                    target_value="SBP < 120, HbA1c < 5.7%",
                    impact_weight=0.25,
                    impact_direction=ImpactDirection.INCREASES_RISK,
                    category="BIOMETRIC",
                    explanation="Co-occurring chronic hypertension and hyperglycemia are primary drivers of diabetic nephropathy (DEMO PROXY).",
                    evidence_citation="ADA/KDIGO Consensus on Diabetes and Chronic Kidney Disease (Heuristic Proxy)",
                ))
            else:
                contributions.append(RiskFactorContribution(
                    feature="Absence of Major Nephrotoxic Drivers",
                    observed_value="Normal systemic pressure and glycemia",
                    target_value="Maintain normotension",
                    impact_weight=0.10,
                    impact_direction=ImpactDirection.DECREASES_RISK,
                    category="BIOMETRIC",
                    explanation="Absence of hypertensive and diabetic vascular strain.",
                    evidence_citation="KDIGO Prevention Guidance",
                ))

        conf = self.confidence(clean_inputs)
        next_step = self.recommended_next_step(category, contributions)

        prov = dict(self.provenance())
        if not is_valid:
            prov["validation_status"] = "DEMONSTRATION_ONLY"

        return DomainRiskResult(
            risk_domain=self.domain,
            score=round(score, 2),
            risk_category=category,
            model_version=self.version(),
            input_snapshot=clean_inputs,
            risk_factor_contributions=contributions,
            confidence=round(conf, 2),
            limitations=limits,
            recommended_next_step=next_step,
            is_clinically_validated=is_valid,
            provenance=prov,
        )

    def explain(self, inputs: Dict[str, Any], result: DomainRiskResult) -> List[RiskFactorContribution]:
        return result.risk_factor_contributions

    def recommended_next_step(self, category: RiskCategory, contributions: List[RiskFactorContribution]) -> str:
        if category == RiskCategory.HIGH:
            return "Urgent nephrology or physician referral; repeat serum creatinine and urine ACR; review nephrotoxic medications (NSAIDs); blood pressure control (<130/80)."
        elif category == RiskCategory.MODERATE:
            return "Order spot urine albumin-to-creatinine ratio (uACR); repeat eGFR in 90 days; maintain strict glycemic and blood pressure targets."
        return "Routine annual metabolic monitoring."
