from datetime import datetime, date, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import uuid

from packages.types.enums import InterventionPillar


class DailyTask(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    day: int                          # 1 to 30
    pillar: InterventionPillar
    title: str
    description: str
    target_metric: Optional[str] = None
    completed: bool = False
    completed_at: Optional[datetime] = None


class PlanModificationRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str
    actor_id: str
    actor_role: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version_before: int
    version_after: int
    fields_modified: List[str] = Field(default_factory=list)
    reason: Optional[str] = None
    notes: Optional[str] = None


class CitizenCheckIn(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    citizen_id: str
    plan_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    feeling: str = "Normal"  # Energetic | Normal | Tired | Unwell
    medication_adherence: bool = True
    symptoms: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    tasks_completed_count: int = 0


DEFAULT_CARE_PLAN_LIMITATIONS = [
    "CRITICAL MEDICATION SAFETY: SevaHealth AI does not prescribe, modify, or discontinue medications.",
    "Lifestyle and preventive recommendations complement but never replace physician guidance.",
    "Clinical review required for severe, atypical, or deteriorating symptoms.",
    "Interventions are not intended for acute emergency care; contact emergency services (108) immediately for distress."
]


class CarePlan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    citizen_id: str
    risk_assessment_id: str
    title: str
    focus_domain: str                 # e.g., "Metabolic Balance & Blood Pressure Control"
    start_date: date
    end_date: date
    adherence_percentage: float = 0.0 # Completed tasks / total tasks
    nutrition_guidance: str
    activity_guidance: str
    sleep_guidance: str = "Consistent 7-8 hours nightly sleep with screen cutoff 45 mins prior."
    stress_guidance: str = "Daily 5-minute diaphragmatic breathing and stress reduction."
    daily_tasks: List[DailyTask] = []
    
    # Extended fields for TC-PLN-001 & TC-PLN-002
    goals: List[str] = Field(default_factory=list)
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    frequency: str = "Daily tasks with weekly milestones"
    follow_up_timing: str = "Day 7 (ASHA check-in), Day 14 (Nurse review), Day 30 (Clinician re-assessment)"
    limitations: List[str] = Field(default_factory=lambda: list(DEFAULT_CARE_PLAN_LIMITATIONS))
    version: int = 1
    history: List[PlanModificationRecord] = Field(default_factory=list)
    
    clinician_reviewed: bool = False
    clinician_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

