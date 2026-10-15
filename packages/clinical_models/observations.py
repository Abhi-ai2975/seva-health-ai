from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, model_validator
import uuid


from typing import Optional, Dict, Any


class ObservationCreate(BaseModel):
    citizen_id: str
    code: str                  # e.g., "SYSTOLIC_BP", "HBA1C", "BMI", "HEIGHT", "WEIGHT"
    value: float
    unit: str
    recorded_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "MANUAL_ENTRY"  # MANUAL_ENTRY | WEARABLE | LAB_REPORT | CLINICAL_DEVICE
    source_event_id: Optional[str] = None  # Unique event ID for idempotency (TC-SCR-006)
    provenance: Optional[Dict[str, Any]] = None  # Provenance tracking (TC-SCR-007)


class Observation(ObservationCreate):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = "tenant-karnataka-moh"
    loinc_code: str = ""
    display_name: str = ""
    is_abnormal: bool = False
    has_conflict: bool = False             # Conflicting observation flag (TC-SCR-007)
    conflict_details: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "citizen_id": self.citizen_id,
            "code": self.code,
            "value": self.value,
            "unit": self.unit,
            "loinc_code": self.loinc_code,
            "display_name": self.display_name,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
            "created_at": self.created_at.isoformat() if hasattr(self, "created_at") and self.created_at else None,
            "source": self.source,
            "source_event_id": self.source_event_id,
            "provenance": self.provenance or {},
            "is_abnormal": self.is_abnormal,
            "has_conflict": self.has_conflict,
            "conflict_details": self.conflict_details or {},
        }


class VitalsPayload(BaseModel):
    """Vitals and lab bundle submitted during screening or follow-up."""
    systolic_bp: Optional[float] = None
    diastolic_bp: Optional[float] = None
    heart_rate: Optional[float] = None
    fasting_glucose: Optional[float] = None
    hba1c: Optional[float] = None
    total_cholesterol: Optional[float] = None
    hdl_cholesterol: Optional[float] = None
    triglycerides: Optional[float] = None
    bmi: Optional[float] = None
    waist_circumference: Optional[float] = None
    egfr: Optional[float] = None

    @model_validator(mode="after")
    def validate_clinical_bounds(self):
        # Blood pressure plausibility validation
        if self.systolic_bp is not None:
            if self.systolic_bp < 40.0 or self.systolic_bp > 300.0:
                raise ValueError(
                    f"Invalid systolic blood pressure: {self.systolic_bp} mmHg. "
                    "Must be clinically plausible between 40 and 300 mmHg."
                )
        if self.diastolic_bp is not None:
            if self.diastolic_bp < 30.0 or self.diastolic_bp > 200.0:
                raise ValueError(
                    f"Invalid diastolic blood pressure: {self.diastolic_bp} mmHg. "
                    "Must be clinically plausible between 30 and 200 mmHg."
                )
        if self.systolic_bp is not None and self.diastolic_bp is not None:
            if self.systolic_bp <= self.diastolic_bp:
                raise ValueError(
                    f"Invalid blood pressure: systolic ({self.systolic_bp} mmHg) must be "
                    f"strictly greater than diastolic ({self.diastolic_bp} mmHg)."
                )
        return self

