"""AI Safety and Clinical Guardrails Framework for SevaHealth AI.

Enforces:
1. Every AI clinical output carries:
   - source data
   - timestamp
   - model/agent version
   - confidence
   - limitations
   - human verification state
2. Strict safety rules for:
   - critical symptoms
   - very abnormal measurements
   - rapid deterioration
   - missing essential information
   - conflicting measurements
3. Emergency routing to human/emergency workflows (halting prolonged conversational AI).
4. Safety banners:
   - "This is a risk assessment, not a diagnosis."
   - "AI-generated information must be reviewed by a healthcare professional."
   - "Seek urgent medical care for emergency symptoms."
5. Red-team protection against:
   - hallucination
   - fabricated patient data
   - medication advice
   - diagnosis claims
   - prompt injection
   - unauthorized data access & cross-patient leakage
   - unsafe escalation

DISCLAIMER: For research and demonstration purposes only. Not clinically validated.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from packages.ai_schemas.safety import (
    ClinicalSafetyEnvelope,
    EmergencyRoutingDetails,
    HumanVerificationState,
    SafetyBanner,
)


class SafetyEvaluationResult(BaseModel):
    """Result of comprehensive AI safety audit."""
    is_safe: bool = True
    is_emergency: bool = False
    emergency_routing: Optional[EmergencyRoutingDetails] = None
    critical_symptoms_detected: List[str] = Field(default_factory=list)
    abnormal_measurements_detected: List[str] = Field(default_factory=list)
    rapid_deteriorations_detected: List[str] = Field(default_factory=list)
    missing_essential_info: List[str] = Field(default_factory=list)
    conflicting_measurements: List[str] = Field(default_factory=list)
    prohibited_terms_detected: List[str] = Field(default_factory=list)
    prompt_injections_detected: List[str] = Field(default_factory=list)
    safety_banners: List[str] = Field(default_factory=list)
    calculated_confidence: float = 1.0
    limitations: List[str] = Field(default_factory=list)
    sanitized_response: str = ""
    envelope: Optional[ClinicalSafetyEnvelope] = None


class ClinicalSafetyEngine:
    """Comprehensive clinical safety, explainability, and guardrails engine."""

    # Red-team prohibited diagnosis and prescription patterns
    PROHIBITED_DIAGNOSIS_PATTERNS = [
        r"\bi diagnose you with\b",
        r"\bi have diagnosed you with\b",
        r"\bmy diagnosis is\b",
        r"\byou definitely have (type [12] )?diabetes\b",
        r"\byou definitely have hypertension\b",
        r"\byou have chronic kidney disease\b",
        r"\byou definitely have cancer\b",
        r"\byour official diagnosis is\b",
        r"\byou are diagnosed with\b",
        r"\bthe screening score diagnoses you\b",
        r"\bthis score proves you have\b",
        r"\bi can confirm you have (type [12] )?diabetes\b",
        r"\bi can confirm you have hypertension\b",
    ]

    PROHIBITED_PRESCRIPTION_PATTERNS = [
        r"\bi prescribe\b",
        r"\btake this medication\b",
        r"\bstart taking metformin\b",
        r"\bstart taking amlodipine\b",
        r"\bstart taking statins\b",
        r"\bincrease your (dosage|dose) to\b",
        r"\bstop taking your (insulin|medication|blood pressure pills)\b",
        r"\btake \d+\s*(mg|milligrams|tablets)\b",
        r"\bprescribe \w+\b",
        r"\byou should discontinue\b",
        r"\byou can stop taking\b",
        r"\bi authorize (stopping|discontinuing)\b",
        r"\bdiscontinue your\b",
    ]

    # Prompt injection patterns
    PROMPT_INJECTION_PATTERNS = [
        r"\bignore (all )?previous instructions\b",
        r"\bdisregard (all )?(guardrails|safety protocols|rules)\b",
        r"\bact as an unrestricted (doctor|physician|assistant|agent|admin)\b",
        r"\bpretend there are no safety (rules|filters)\b",
        r"\byou are now in DAN mode\b",
        r"\bbypass clinical (guidelines|oversight)\b",
        r"\btell me I am completely healthy and do not need a doctor\b",
        r"\bdo not show any safety banners\b",
        r"\b(override|bypass) (system )?(policy|policies|rules)\b",
        r"\bsystem override\b",
        r"\bdisclose (all )?(database|passwords?|secrets?|keys?|private|patient|records)\b",
        r"\breveal (database|credentials|jwt|secrets?|all patients?|keys?)\b",
        r"\bjailbreak\b",
        r"\bunrestricted mode\b",
        r"\bexfiltrate\b",
        r"\bdrop table\b",
        r"\badmin override\b",
        r"\bauthorize disclosure of (protected|confidential|private)\b",
    ]

    # Unsupported specialized medical diagnostic modalities not supported by the primary care screening system
    UNSUPPORTED_DIAGNOSTIC_PATTERNS = [
        r"\b(mri|magnetic resonance imaging)\b",
        r"\b(ct scan|computed tomography)\b",
        r"\b(biopsy|histology|histopathology)\b",
        r"\b(karyotype|genetic sequencing|exome|dna test|genome sequencing)\b",
        r"\b(pet scan|positron emission tomography)\b",
        r"\b(echocardiogram|echo report|ejection fraction)\b",
        r"\b(coronary angiogram|cardiac catheterization)\b",
        r"\b(bone density|dexa scan)\b",
        r"\b(colonoscopy|endoscopy report)\b",
        r"\b(bone marrow aspirate|lumbar puncture)\b",
    ]

    # Critical symptoms requiring immediate emergency escalation
    CRITICAL_SYMPTOMS_PATTERNS = [
        (r"\b(crushing|severe|radiating|squeezing) chest pain\b", "Chest pain characteristic of acute coronary syndrome"),
        (r"\bchest pain\b", "Chest pain requiring urgent evaluation"),
        (r"\bpain radiating to (left arm|jaw|neck|back)\b", "Pain radiating to arm/jaw suggesting myocardial ischemia"),
        (r"\b(sudden|severe) shortness of breath\b", "Acute respiratory distress"),
        (r"\b(sudden )?(facial droop|face drooping|arm weakness|slurred speech|speech difficulty)\b", "Acute focal neurological deficit (FAST stroke symptoms)"),
        (r"\b(worst headache of my life|thunderclap headache)\b", "Severe acute headache suggestive of intracranial hemorrhage"),
        (r"\b(passed out|fainted|loss of consciousness|syncope)\b", "Transient loss of consciousness / syncope"),
        (r"\b(coughing up blood|hemoptysis)\b", "Hemoptysis / acute pulmonary hemorrhage"),
        (r"\b(acute confusion|unresponsive)\b", "Altered mental status / unresponsiveness"),
    ]

    # Physiological plausibility bounds
    PHYSIOLOGICAL_RANGES = {
        "systolic_bp": (70.0, 260.0),
        "diastolic_bp": (40.0, 150.0),
        "heart_rate": (35.0, 220.0),
        "fasting_glucose": (40.0, 600.0),
        "hba1c": (3.5, 18.0),
        "spo2": (50.0, 100.0),
        "height_cm": (60.0, 250.0),
        "weight_kg": (20.0, 280.0),
    }

    # 1. Critical Symptoms Audit
    @classmethod
    def check_critical_symptoms(cls, text_or_symptoms: Union[str, List[str]]) -> List[str]:
        detected = []
        if isinstance(text_or_symptoms, list):
            search_text = " ".join(str(s) for s in text_or_symptoms)
        else:
            search_text = str(text_or_symptoms)

        for pattern, description in cls.CRITICAL_SYMPTOMS_PATTERNS:
            if re.search(pattern, search_text, re.IGNORECASE):
                detected.append(description)

        return list(dict.fromkeys(detected))

    @staticmethod
    def _extract_numeric(val: Any) -> Optional[float]:
        if val is None:
            return None
        if isinstance(val, dict):
            val = val.get("value")
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    # 2. Very Abnormal Measurements Audit
    @classmethod
    def check_abnormal_measurements(cls, vitals: Dict[str, Any]) -> List[str]:
        abnormal = []

        sbp = cls._extract_numeric(vitals.get("systolic_bp") or vitals.get("sbp"))
        dbp = cls._extract_numeric(vitals.get("diastolic_bp") or vitals.get("dbp"))
        spo2 = cls._extract_numeric(vitals.get("spo2") or vitals.get("oxygen_saturation"))
        hr = cls._extract_numeric(vitals.get("heart_rate") or vitals.get("pulse"))
        glucose = cls._extract_numeric(vitals.get("fasting_glucose") or vitals.get("blood_glucose") or vitals.get("glucose"))

        # Blood pressure emergencies
        if sbp is not None:
            if sbp >= 180.0:
                abnormal.append(f"Hypertensive crisis: Systolic BP {sbp:.0f} mmHg (>= 180 mmHg)")
            elif sbp < 80.0:
                abnormal.append(f"Severe hypotension: Systolic BP {sbp:.0f} mmHg (< 80 mmHg)")

        if dbp is not None:
            if dbp >= 120.0:
                abnormal.append(f"Hypertensive crisis: Diastolic BP {dbp:.0f} mmHg (>= 120 mmHg)")
            elif dbp < 50.0:
                abnormal.append(f"Severe hypotension: Diastolic BP {dbp:.0f} mmHg (< 50 mmHg)")

        # Oxygen saturation
        if spo2 is not None and spo2 < 90.0:
            abnormal.append(f"Critical hypoxia: SpO2 {spo2:.0f}% (< 90%)")

        # Heart rate
        if hr is not None:
            if hr > 140.0:
                abnormal.append(f"Severe tachycardia: Heart rate {hr:.0f} bpm (> 140 bpm)")
            elif hr < 40.0:
                abnormal.append(f"Severe bradycardia: Heart rate {hr:.0f} bpm (< 40 bpm)")

        # Glucose extremes
        if glucose is not None:
            if glucose >= 300.0:
                abnormal.append(f"Severe hyperglycemia / ketoacidosis risk: Fasting Glucose {glucose:.0f} mg/dL (>= 300 mg/dL)")
            elif glucose < 54.0:
                abnormal.append(f"Severe neuroglycopenic hypoglycemia: Blood Glucose {glucose:.0f} mg/dL (< 54 mg/dL)")

        return abnormal

    # 3. Rapid Deterioration Audit
    @classmethod
    def check_rapid_deterioration(
        cls,
        current_vitals: Dict[str, Any],
        baseline_vitals: Optional[Dict[str, Any]] = None,
        trajectory_slope: Optional[float] = None,
    ) -> List[str]:
        deteriorations = []

        if baseline_vitals:
            curr_sbp = cls._extract_numeric(current_vitals.get("systolic_bp"))
            base_sbp = cls._extract_numeric(baseline_vitals.get("systolic_bp"))
            if curr_sbp is not None and base_sbp is not None:
                delta_sbp = curr_sbp - base_sbp
                if delta_sbp >= 30.0:
                    deteriorations.append(f"Rapid acute systolic BP elevation: +{delta_sbp:.0f} mmHg from baseline")

            curr_spo2 = cls._extract_numeric(current_vitals.get("spo2"))
            base_spo2 = cls._extract_numeric(baseline_vitals.get("spo2"))
            if curr_spo2 is not None and base_spo2 is not None:
                delta_spo2 = base_spo2 - curr_spo2
                if delta_spo2 >= 4.0:
                    deteriorations.append(f"Acute oxygen desaturation: -{delta_spo2:.0f}% drop from baseline")

            curr_hr = cls._extract_numeric(current_vitals.get("heart_rate"))
            base_hr = cls._extract_numeric(baseline_vitals.get("heart_rate"))
            if curr_hr is not None and base_hr is not None:
                delta_hr = curr_hr - base_hr
                if delta_hr >= 35.0:
                    deteriorations.append(f"Acute resting tachycardia shift: +{delta_hr:.0f} bpm increase")

        if trajectory_slope is not None and trajectory_slope <= -0.35:
            deteriorations.append(f"Rapid negative longitudinal risk trajectory: slope {trajectory_slope:.2f}")

        return deteriorations

    # 4. Missing Essential Information Audit
    @classmethod
    def check_missing_essential_information(
        cls,
        available_data: Dict[str, Any],
        required_domains: Optional[List[str]] = None,
    ) -> Tuple[List[str], float, List[str]]:
        """Checks for missing critical inputs, scales confidence score, and returns clinical limitations."""
        missing = []
        limitations = []
        confidence = 1.0

        # Mandatory foundational attributes
        if "age" not in available_data or available_data.get("age") is None:
            missing.append("age")
            limitations.append("Patient age is unrecorded; age-adjusted epidemiological risk is unanchored.")
            confidence -= 0.25

        if "sex" not in available_data or available_data.get("sex") is None:
            missing.append("sex")
            limitations.append("Sex is unrecorded; sex-specific cardiovascular risk adjustments cannot be applied.")
            confidence -= 0.15

        # Cardiovascular / Hypertension essentials
        has_bp = ("systolic_bp" in available_data and available_data.get("systolic_bp") is not None) or (
            "diastolic_bp" in available_data and available_data.get("diastolic_bp") is not None
        )
        if not has_bp:
            missing.append("blood_pressure")
            limitations.append("Missing blood pressure vitals; hemodynamic and hypertension staging unavailable.")
            confidence -= 0.30

        # Metabolic / Glycemic essentials
        has_glucose = ("fasting_glucose" in available_data and available_data.get("fasting_glucose") is not None) or (
            "hba1c" in available_data and available_data.get("hba1c") is not None
        )
        if not has_glucose:
            missing.append("glycemic_biomarkers")
            limitations.append("Laboratory glycemic biomarkers (fasting glucose / HbA1c) are missing; diabetes risk is approximated.")
            confidence -= 0.20

        # Renal essentials
        if "serum_creatinine" not in available_data or available_data.get("serum_creatinine") is None:
            missing.append("serum_creatinine")
            limitations.append("Serum creatinine unavailable; CKD/eGFR risk cannot be computed and is excluded from scoring.")
            confidence -= 0.10

        confidence = max(0.10, round(confidence, 2))
        return missing, confidence, limitations

    # 5. Conflicting Measurements Audit
    @classmethod
    def check_conflicting_measurements(cls, data: Dict[str, Any]) -> List[str]:
        conflicts = []

        sbp = cls._extract_numeric(data.get("systolic_bp"))
        dbp = cls._extract_numeric(data.get("diastolic_bp"))
        if sbp is not None and dbp is not None:
            if dbp >= sbp:
                conflicts.append(f"Physiologically impossible blood pressure: Diastolic ({dbp}) >= Systolic ({sbp})")
            elif (sbp - dbp) < 10.0:
                conflicts.append(f"Extremely narrow pulse pressure ({sbp - dbp:.0f} mmHg) with normal systolic BP")

        # Contradictory smoking profile
        smoke_status = cls._extract_numeric(data.get("smoking_status"))
        cigs_per_day = cls._extract_numeric(data.get("cigarettes_per_day"))
        if smoke_status == 0 and cigs_per_day is not None and cigs_per_day > 0:
            conflicts.append(f"Contradictory tobacco profile: smoking_status is 'Non-smoker' but reports {cigs_per_day} cigarettes/day")

        # Plausibility bounds
        for key, (low, high) in cls.PHYSIOLOGICAL_RANGES.items():
            fval = cls._extract_numeric(data.get(key))
            if fval is not None:
                if fval < low or fval > high:
                    conflicts.append(f"Measurement out of physiological bounds: {key}={fval} (Expected {low} - {high})")

        return conflicts

    # 6. Prompt Injection Audit
    @classmethod
    def check_prompt_injection(cls, user_text: str) -> List[str]:
        violations = []
        for pattern in cls.PROMPT_INJECTION_PATTERNS:
            if re.search(pattern, user_text, re.IGNORECASE):
                violations.append(f"Prompt injection pattern detected: '{pattern}'")
        return violations

    @classmethod
    def defang_untrusted_text(cls, text: str) -> str:
        """Sanitizes and neutralizes adversarial instructions from untrusted text (e.g. uploaded documents or notes)."""
        sanitized = text
        for pattern in cls.PROMPT_INJECTION_PATTERNS:
            if re.search(pattern, sanitized, re.IGNORECASE):
                sanitized = re.sub(
                    pattern,
                    "[FILTERED UNTRUSTED INSTRUCTION: Potential prompt injection neutralized]",
                    sanitized,
                    flags=re.IGNORECASE,
                )
        return sanitized

    @classmethod
    def is_unsupported_diagnostic_query(cls, text: str) -> bool:
        """Identifies medical queries requiring specialized diagnostic modalities unsupported by primary care screening."""
        for pattern in cls.UNSUPPORTED_DIAGNOSTIC_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    # 7. Red-Team Prohibited Terms Audit (Medication advice & diagnosis claims)
    @classmethod
    def check_prohibited_terms(cls, response_text: str) -> Tuple[List[str], str]:
        violations = []
        sanitized = response_text

        # Diagnosis checks
        for pat in cls.PROHIBITED_DIAGNOSIS_PATTERNS:
            if re.search(pat, sanitized, re.IGNORECASE):
                violations.append(f"Prohibited autonomous diagnosis claim matching: {pat}")
                sanitized = re.sub(
                    pat,
                    "[NON-DIAGNOSTIC NOTICE: Risk assessment only - Clinical diagnosis requires human physician evaluation]",
                    sanitized,
                    flags=re.IGNORECASE,
                )

        # Prescription checks
        for pat in cls.PROHIBITED_PRESCRIPTION_PATTERNS:
            if re.search(pat, sanitized, re.IGNORECASE):
                violations.append(f"Prohibited autonomous medication/prescription advice matching: {pat}")
                sanitized = re.sub(
                    pat,
                    "[PRESCRIPTION NOTICE: AI cannot prescribe medication - Consult registered medical practitioner]",
                    sanitized,
                    flags=re.IGNORECASE,
                )

        return violations, sanitized

    @classmethod
    def audit_ai_response(cls, text: str) -> Tuple[bool, str]:
        """Audits AI response for prohibited clinical claims (diagnoses or prescriptions)."""
        violations, sanitized = cls.check_prohibited_terms(text)
        return (len(violations) == 0, sanitized)

    # 8. Master Safety Evaluation Pipeline
    @classmethod
    def evaluate_clinical_interaction(
        cls,
        patient_data: Dict[str, Any],
        user_prompt: str,
        ai_response_text: str,
        baseline_data: Optional[Dict[str, Any]] = None,
        trajectory_slope: Optional[float] = None,
        model_version: str = "v1.0.0",
        agent_version: str = "v1.0.0",
    ) -> SafetyEvaluationResult:
        """Runs the entire clinical safety guardrail suite."""
        # A. Prompt injection detection
        injection_flags = cls.check_prompt_injection(user_prompt)

        # B. Critical symptoms detection (in prompt or reported symptoms)
        combined_symptom_text = user_prompt + " " + str(patient_data.get("symptoms", ""))
        critical_symptoms = cls.check_critical_symptoms(combined_symptom_text)

        # C. Very abnormal measurements
        abnormal_vitals = cls.check_abnormal_measurements(patient_data)

        # D. Rapid deterioration
        rapid_deteriorations = cls.check_rapid_deterioration(
            current_vitals=patient_data,
            baseline_vitals=baseline_data,
            trajectory_slope=trajectory_slope,
        )

        # E. Missing essential information
        missing_info, calculated_conf, limitations = cls.check_missing_essential_information(patient_data)

        # F. Conflicting measurements
        conflicts = cls.check_conflicting_measurements(patient_data)

        # G. Prohibited clinical claims in AI response
        prohibited_terms, sanitized_text = cls.check_prohibited_terms(ai_response_text)

        # H. Determine emergency state
        is_emergency = (len(critical_symptoms) > 0) or (len(abnormal_vitals) > 0)

        # Build banners
        banners = [
            SafetyBanner.RISK_ASSESSMENT.value,
            SafetyBanner.PROFESSIONAL_REVIEW.value,
        ]

        emergency_routing = None
        if is_emergency:
            banners.append(SafetyBanner.EMERGENCY.value)
            all_reasons = critical_symptoms + abnormal_vitals
            emergency_routing = EmergencyRoutingDetails(
                is_emergency=True,
                urgency_level="CRITICAL_EMERGENCY",
                trigger_reasons=all_reasons,
                recommended_action=(
                    "EMERGENCY PROTOCOL ACTIVATED: Immediately proceed to the nearest emergency department or primary health center. "
                    "Call 108 or 112 for ambulance services. Do not wait for routine follow-up."
                ),
            )
            # Halting prolonged casual AI response in emergency situations
            emergency_alert_text = (
                "⚠️ URGENT CLINICAL EMERGENCY DETECTED ⚠️\n\n"
                f"{SafetyBanner.EMERGENCY.value}\n\n"
                "The symptoms or physiological measurements reported indicate potential acute medical risk:\n"
                + "\n".join(f"- {r}" for r in all_reasons)
                + "\n\n"
                "RECOMMENDED ACTION:\n"
                "- Call 108 (Ambulance) or 112 immediately.\n"
                "- Seek urgent in-person medical evaluation at the nearest Primary Health Center or hospital.\n"
                "- Do not rely on AI health advice for active emergency symptoms."
            )
            sanitized_text = emergency_alert_text

        # Create mandatory safety envelope
        envelope = ClinicalSafetyEnvelope(
            source_data={k: v for k, v in patient_data.items() if k not in ["password", "token"]},
            timestamp=datetime.now(timezone.utc).isoformat(),
            model_version=model_version,
            agent_version=agent_version,
            confidence=calculated_conf,
            limitations=limitations,
            human_verification_state=HumanVerificationState.PENDING_REVIEW,
            safety_banners=banners,
            emergency_routing_triggered=is_emergency,
            emergency_routing=emergency_routing,
            safety_violations_detected=prohibited_terms + injection_flags + conflicts,
        )

        is_safe = (len(prohibited_terms) == 0) and (len(injection_flags) == 0) and (len(conflicts) == 0)

        return SafetyEvaluationResult(
            is_safe=is_safe,
            is_emergency=is_emergency,
            emergency_routing=emergency_routing,
            critical_symptoms_detected=critical_symptoms,
            abnormal_measurements_detected=abnormal_vitals,
            rapid_deteriorations_detected=rapid_deteriorations,
            missing_essential_info=missing_info,
            conflicting_measurements=conflicts,
            prohibited_terms_detected=prohibited_terms,
            prompt_injections_detected=injection_flags,
            safety_banners=banners,
            calculated_confidence=calculated_conf,
            limitations=limitations,
            sanitized_response=sanitized_text,
            envelope=envelope,
        )


# Backward-compatible alias for existing imports
ClinicalSafetyEnforcer = ClinicalSafetyEngine
