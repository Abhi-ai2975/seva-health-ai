from typing import Dict, Any, List
from packages.clinical_models.observations import Observation
from packages.clinical_models.risk import RiskAssessment


def export_patient_fhir_r4(
    citizen_id: str,
    first_name: str,
    last_name: str,
    gender: str,
    birth_date: str,
    phone: str,
    abha_id: str = None
) -> Dict[str, Any]:
    """Serializes a Citizen into an HL7 FHIR R4 Patient Resource."""
    identifiers = [
        {"system": "https://sevahealth.ai/citizen-id", "value": citizen_id}
    ]
    if abha_id:
        identifiers.append({"system": "https://abdm.gov.in/abha", "value": abha_id})

    return {
        "resourceType": "Patient",
        "id": citizen_id,
        "identifier": identifiers,
        "active": True,
        "name": [{
            "use": "official",
            "family": last_name,
            "given": [first_name]
        }],
        "telecom": [{
            "system": "phone",
            "value": phone,
            "use": "mobile"
        }],
        "gender": gender.lower() if gender in ["MALE", "FEMALE"] else "other",
        "birthDate": birth_date
    }


def export_observation_fhir_r4(obs: Observation) -> Dict[str, Any]:
    """Serializes an Observation into an HL7 FHIR R4 Observation Resource."""
    return {
        "resourceType": "Observation",
        "id": obs.id,
        "status": "final",
        "code": {
            "coding": [{
                "system": "http://loinc.org",
                "code": obs.loinc_code,
                "display": obs.display_name
            }]
        },
        "subject": {
            "reference": f"Patient/{obs.citizen_id}"
        },
        "effectiveDateTime": obs.recorded_at.isoformat(),
        "valueQuantity": {
            "value": obs.value,
            "unit": obs.unit,
            "system": "http://unitsofmeasure.org"
        },
        "interpretation": [{
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                "code": "A" if obs.is_abnormal else "N",
                "display": "Abnormal" if obs.is_abnormal else "Normal"
            }]
        }]
    }


def export_risk_assessment_fhir_r4(risk: RiskAssessment) -> Dict[str, Any]:
    """Serializes a RiskAssessment into an HL7 FHIR R4 RiskAssessment Resource
    with ABDM-aligned validation tagging and demonstration-only labelling (TC-RSK-003).
    """
    is_demo = getattr(risk, "is_demonstration_only", False) or not getattr(risk, "is_clinically_validated", True)

    tags = []
    extensions = []
    notes = []

    if is_demo:
        tags.append({
            "system": "https://sevahealth.ai/clinical-validation",
            "code": "DEMONSTRATION_ONLY",
            "display": "DEMONSTRATION ONLY - NOT CLINICALLY VALIDATED"
        })
        extensions.extend([
            {
                "url": "https://sevahealth.ai/fhir/StructureDefinition/demonstration-only",
                "valueBoolean": True
            },
            {
                "url": "https://sevahealth.ai/fhir/StructureDefinition/clinical-validation-status",
                "valueString": "DEMONSTRATION_ONLY_NOT_CLINICALLY_VALIDATED"
            }
        ])
        warning_msg = (
            getattr(risk, "demonstration_warning", None)
            or "⚠️ DEMONSTRATION ONLY: This model is an unvalidated demonstration proxy for research and testing. NOT CLINICALLY VALIDATED for diagnostic or therapeutic decisions."
        )
        notes.append({"text": warning_msg})
    else:
        tags.append({
            "system": "https://sevahealth.ai/clinical-validation",
            "code": "CLINICALLY_VALIDATED",
            "display": "Clinically Validated Population Screening Guideline"
        })
        extensions.append({
            "url": "https://sevahealth.ai/fhir/StructureDefinition/demonstration-only",
            "valueBoolean": False
        })

    notes.append({"text": risk.safety_disclaimer})

    score_val = round(risk.overall_score, 4) if risk.overall_score is not None else None

    resource: Dict[str, Any] = {
        "resourceType": "RiskAssessment",
        "id": risk.id,
        "meta": {
            "tag": tags
        },
        "extension": extensions,
        "status": "final",
        "subject": {
            "reference": f"Patient/{risk.citizen_id}"
        },
        "occurrenceDateTime": risk.evaluated_at.isoformat(),
        "prediction": [{
            "probabilityDecimal": score_val,
            "qualitativeRisk": {
                "coding": [{
                    "system": "https://sevahealth.ai/risk-tier",
                    "code": risk.overall_tier.value,
                    "display": f"{risk.overall_tier.value} Risk"
                }]
            },
            "rationale": risk.clinical_summary
        }],
        "note": notes
    }

    return resource
