from typing import List, Optional, Dict, Any
from datetime import datetime, date, timezone
from enum import Enum
from pydantic import BaseModel, Field
import uuid

from packages.types.enums import InterventionPillar
from packages.clinical_models.care_plan import DailyTask


class InterventionCategory(str, Enum):
    NUTRITION = "nutrition"
    PHYSICAL_ACTIVITY = "physical_activity"
    SLEEP = "sleep"
    WEIGHT_MANAGEMENT = "weight_management"
    SMOKING_CESSATION = "smoking_cessation"
    ALCOHOL_REDUCTION = "alcohol_reduction"
    STRESS_MANAGEMENT = "stress_management"
    SCREENING_ADHERENCE = "screening_adherence"
    CLINICAL_FOLLOWUP = "clinical_followup"


class PriorityRank(int, Enum):
    SAFETY = 1           # 1. Safety (Prevent adverse events, joint protection, contraindications)
    EVIDENCE = 2         # 2. Evidence (Validated medical/public-health clinical guidelines)
    EXPECTED_BENEFIT = 3 # 3. Expected Benefit (Highest clinical risk reduction impact)
    FEASIBILITY = 4      # 4. Feasibility (Resource availability, budget, mobility)
    PREFERENCE = 5       # 5. Patient Preference (Cuisine, cultural habits, schedule)


class SMARTGoal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    pillar: InterventionCategory
    title: str
    target_metric: str
    timeline_days: int = 30
    clinical_rationale: str


class PrioritizedAction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: InterventionCategory
    title: str
    description: str
    frequency: str                     # e.g., "Daily post-dinner", "3x per week", "Bi-weekly"
    target_metric: Optional[str] = None
    priority_level: PriorityRank
    evidence_citation: str             # e.g., "ICMR-NIN 2024 Dietary Guidelines"
    expected_benefit_score: float      # 0.0 to 1.0 impact weighting
    feasibility_score: float           # 0.0 to 1.0 adjusted for citizen constraints
    preference_match_score: float      # 0.0 to 1.0 adjusted for citizen preferences
    safety_notes: Optional[str] = None


class ScheduledReminder(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: InterventionCategory
    title: str
    scheduled_time: str                # e.g., "07:30 AM", "20:30 PM", "Sundays 10:00 AM"
    frequency: str                     # DAILY | WEEKLY | BI_WEEKLY | POST_MEAL
    channel: str = "PUSH_NOTIFICATION" # PUSH_NOTIFICATION | SMS | WHATSAPP | ASHA_ALERT
    message_content: str


class EducationalModule(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: InterventionCategory
    title: str
    key_takeaway: str
    reading_time_mins: int = 3
    cultural_adaptation: str           # Regional recipe, local language tip, community context
    evidence_summary: str


class MeasurementSchedule(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    biometric: str                     # e.g., "BLOOD_PRESSURE", "FASTING_GLUCOSE", "BODY_WEIGHT", "WAIST_CIRCUMFERENCE"
    frequency: str                     # e.g., "Weekly on Sunday morning", "Day 1, 15, and 30"
    target_range: str                  # e.g., "< 120/80 mmHg", "< 100 mg/dL"
    instructions: str                  # e.g., "Measure seated after 5 minutes of rest, bilateral arm"


class FollowUpEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    day_number: int                    # e.g., Day 7, Day 14, Day 30
    scheduled_date: Optional[date] = None
    provider_role: str                 # "ASHA_WORKER" | "PRIMARY_CARE_NURSE" | "CLINICIAN" | "WELLNESS_COACH"
    channel: str                       # "HOME_VISIT" | "TELE_CONSULT" | "PHC_VISIT" | "CAMP_CHECKIN"
    agenda: str


class PreventionPlanInput(BaseModel):
    """Input payload for generating a personalized 30-day prevention plan."""
    citizen_id: str
    tenant_id: str = "karnataka_state_health"
    risk_profile: Optional[Dict[str, Any]] = None       # Domains, overall_tier, drivers
    risk_trajectory: Optional[str] = "STABLE"           # WORSENING | STABLE | IMPROVING | INSUFFICIENT_DATA
    lifestyle: Dict[str, Any] = {}                      # Diet, activity, sleep, smoking, alcohol, stress
    goals: List[str] = []                               # User-stated or clinician goals
    preferences: Dict[str, Any] = {}                    # Dietary style, preferred exercise, reminder timing
    constraints: List[str] = []                         # Joint pain, shift work, budget, mobility limitations
    available_community_resources: List[Dict[str, Any]] = [] # Parks, PHC yoga, Anganwadi hubs, ASHA circles
    clinician_recommendations: List[str] = []           # Specific clinical orders or restrictions


from packages.clinical_models.care_plan import DailyTask, PlanModificationRecord, CitizenCheckIn, DEFAULT_CARE_PLAN_LIMITATIONS


class ComprehensivePreventionPlan(BaseModel):
    """30-Day Prevention Care Plan adhering to all safety, evidence, and personalization criteria."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    citizen_id: str
    title: str
    focus_domain: str
    start_date: date
    end_date: date
    
    # Core Plan Outputs
    goals: List[SMARTGoal] = []
    actions: List[PrioritizedAction] = []
    daily_tasks: List[DailyTask] = []
    reminders: List[ScheduledReminder] = []
    educational_content: List[EducationalModule] = []
    measurements: List[MeasurementSchedule] = []
    follow_up_schedule: List[FollowUpEvent] = []
    
    # TC-PLN-001 Required Structural Fields
    frequency: str = "Daily activities with weekly milestones"
    follow_up_timing: str = "Day 7 (ASHA check-in), Day 14 (Nurse review), Day 30 (Clinician re-assessment)"
    limitations: List[str] = Field(default_factory=lambda: list(DEFAULT_CARE_PLAN_LIMITATIONS))
    version: int = 1
    history: List[PlanModificationRecord] = Field(default_factory=list)
    
    # Progress & Adherence
    adherence_percentage: float = 0.0
    
    # Clinical Safety & Governance (STRICTLY NON-AUTONOMOUS MEDICATION)
    clinician_medication_disclaimer: str = (
        "CRITICAL MEDICATION SAFETY: SevaHealth AI does NOT prescribe, modify, or discontinue medications. "
        "Never stop or alter prescribed medications without direct consultation with your treating physician. "
        "Any pharmacotherapy modifications require clinician review."
    )
    clinician_workflow_required: bool = False
    clinician_review_status: str = "PENDING"
    clinician_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PlanModificationRequest(BaseModel):
    """Payload submitted by authorized clinician to modify a care plan."""
    goals: Optional[List[str]] = None
    nutrition_guidance: Optional[str] = None
    activity_guidance: Optional[str] = None
    sleep_guidance: Optional[str] = None
    stress_guidance: Optional[str] = None
    limitations: Optional[List[str]] = None
    follow_up_timing: Optional[str] = None
    frequency: Optional[str] = None
    actions: Optional[List[Dict[str, Any]]] = None
    daily_tasks: Optional[List[DailyTask]] = None
    reason: Optional[str] = "Clinical personalization"
    notes: Optional[str] = None


class CitizenCheckInCreate(BaseModel):
    """Payload submitted during a citizen daily check-in."""
    feeling: str = "Normal"  # Energetic | Normal | Tired | Unwell
    medication_adherence: bool = True
    symptoms: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    tasks_completed_count: Optional[int] = 0

