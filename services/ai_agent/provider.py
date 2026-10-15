from typing import Dict, Any, List, Optional
import json
from packages.config.settings import settings
from packages.ai_schemas.schemas import LLMRiskExplanationOutput, LLMSOAPSummaryOutput


class AIServiceUnavailableError(RuntimeError):
    """Raised when the AI generation/explanation service is disabled or unreachable."""
    pass


_AI_PROVIDER_ENABLED = True


def set_ai_provider_enabled(enabled: bool) -> None:
    """Configures whether the AI provider is available (used for TC-AI-006 fault-tolerance testing)."""
    global _AI_PROVIDER_ENABLED
    _AI_PROVIDER_ENABLED = enabled


def is_ai_provider_enabled() -> bool:
    """Returns True if the AI provider is currently enabled."""
    return _AI_PROVIDER_ENABLED


class AIProviderInterface:
    async def explain_risk(
        self,
        citizen_name: str,
        vitals_summary: str,
        risk_score: float,
        top_drivers: Optional[List[str]] = None,
        protective_factors: Optional[List[str]] = None,
        observations: Optional[Dict[str, Any]] = None,
        limitations: Optional[List[str]] = None,
    ) -> LLMRiskExplanationOutput:
        raise NotImplementedError

    async def generate_soap_note(self, citizen_name: str, observations: Dict[str, Any], risk_tier: str) -> LLMSOAPSummaryOutput:
        raise NotImplementedError


class DeterministicMockProvider(AIProviderInterface):
    """High-fidelity, mathematically consistent mock provider for offline evaluation (adapted from refactoragent)."""

    async def explain_risk(
        self,
        citizen_name: str,
        vitals_summary: str,
        risk_score: float,
        top_drivers: Optional[List[str]] = None,
        protective_factors: Optional[List[str]] = None,
        observations: Optional[Dict[str, Any]] = None,
        limitations: Optional[List[str]] = None,
    ) -> LLMRiskExplanationOutput:
        disclaimer = "This is a preventive risk assessment, not a clinical diagnosis. It reflects physiological patterns that warrant preventive attention."

        if risk_score >= 0.70:
            summary = (
                f"{citizen_name} is currently exhibiting high physiological strain with an elevated risk score of {int(risk_score * 100)}%. "
                f"Observed biometrics ({vitals_summary}) indicate metabolic and cardiovascular parameters above target thresholds. {disclaimer}"
            )
            drivers = top_drivers or [
                "Elevated systolic vascular pressure consistently above 135 mmHg",
                "Impaired fasting blood glucose / elevated HbA1c indicative of insulin resistance",
                "Central abdominal adiposity exceeding South Asian recommended cutoffs",
            ]
            strengths = protective_factors or ["Sufficient baseline resting heart rate variability indicates recovery potential."]
            focus = "Reduce dietary sodium and incorporate whole ragi/millets in place of white rice."
        elif risk_score >= 0.40:
            summary = (
                f"{citizen_name} is in a moderate risk window ({int(risk_score * 100)}%) where timely preventive lifestyle adjustments "
                f"can successfully reverse early metabolic deterioration. Observed biometrics ({vitals_summary}) indicate key focus areas. {disclaimer}"
            )
            drivers = top_drivers or [
                "Sedentary lifestyle with fewer than 30 minutes of daily physical movement",
                "Mildly elevated diastolic blood pressure",
            ]
            strengths = protective_factors or ["Non-tobacco user with healthy kidney filtration markers."]
            focus = "Daily 15-minute post-dinner brisk walks."
        else:
            summary = (
                f"{citizen_name} maintains a robust preventive physiological profile with low chronic disease indicators ({int(risk_score * 100)}%). "
                f"Observed biometrics ({vitals_summary}). {disclaimer}"
            )
            drivers = top_drivers or ["Normal lifestyle maintenance required."]
            strengths = protective_factors or ["Optimal blood pressure and glycemic markers.", "Regular active daily movement."]
            focus = "Sustain current active habits and attend annual screening."

        safety_reminder = "Clinical review recommended. This is preventive screening, not a medical diagnosis."
        if limitations:
            safety_reminder += f" Limitations/Uncertainties: {'; '.join(limitations)}."

        return LLMRiskExplanationOutput(
            plain_summary=summary,
            key_drivers=drivers,
            protective_strengths=strengths,
            recommended_focus_area=focus,
            safety_reminder=safety_reminder,
        )

    async def generate_soap_note(self, citizen_name: str, observations: Dict[str, Any], risk_tier: str) -> LLMSOAPSummaryOutput:
        return LLMSOAPSummaryOutput(
            subjective=f"Patient {citizen_name} participated in community preventive health screening.",
            objective=f"Observed vitals: {json.dumps(observations)}. Evaluated Risk Tier: {risk_tier}.",
            assessment=f"Clinical decision support suggests {risk_tier} risk of lifestyle disease progression.",
            plan="1. Clinician verification of biometrics. 2. Activate 30-day preventive care plan. 3. Advise follow-up in 30 days.",
            clinical_flags=["Clinical review recommended. Not an autonomous prescription."],
        )


def get_ai_provider() -> AIProviderInterface:
    """Returns the configured AI provider, defaulting to DeterministicMockProvider for offline resilience."""
    if not is_ai_provider_enabled():
        raise AIServiceUnavailableError("AI provider service is currently offline or disabled (TC-AI-006).")
    return DeterministicMockProvider()
