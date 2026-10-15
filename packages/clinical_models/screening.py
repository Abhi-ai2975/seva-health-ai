from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
import uuid

from packages.clinical_models.observations import VitalsPayload


class CBACSurvey(BaseModel):
    """Community-Based Assessment Checklist for Early NCD Screening in India."""
    age_over_30: bool
    tobacco_user: bool               # Smoked or smokeless (gutkha/khaini/bidi)
    alcohol_consumption: bool        # Regular consumption
    waist_circumference_exceeded: bool
    physical_activity_below_150min: bool
    family_history_diabetes_or_htn: bool
    symptoms: List[str] = []         # Shortness of breath, persistent cough, etc.


class IDRSSurvey(BaseModel):
    """Indian Diabetes Risk Score (ICMR-INDIAB Standard: 0 to 100 points)."""
    age_category: str                # "<35" (0 pts), "35-49" (20 pts), ">=50" (30 pts)
    waist_category: str              # Male: <90 (0), 90-99 (10), >=100 (20) | Female: <80, 80-89, >=90
    physical_activity: str           # "Vigorous" (0), "Moderate" (10), "Sedentary" (20), "None" (30)
    family_history: str              # "None" (0), "One parent" (10), "Both parents" (20)


class ScreeningSessionCreate(BaseModel):
    citizen_id: str
    cbac: Optional[CBACSurvey] = None
    idrs: Optional[IDRSSurvey] = None
    vitals: Optional[VitalsPayload] = None
    notes: Optional[str] = None
    status: Optional[str] = "COMPLETED"
    questionnaire_version: Optional[str] = "1.0.0"


class ScreeningSession(ScreeningSessionCreate):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    conducted_by_id: str             # Health worker or citizen ID
    conducted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    calculated_idrs_score: Optional[int] = None
    calculated_cbac_score: Optional[int] = None
    status: str = "COMPLETED"
    questionnaire_version: str = "1.0.0"

