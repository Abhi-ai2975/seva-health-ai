from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple, Union
from pydantic import BaseModel, Field
import math
import structlog

logger = structlog.get_logger(__name__)


class RiskCategory(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ImpactDirection(str, Enum):
    INCREASES_RISK = "INCREASES_RISK"
    DECREASES_RISK = "DECREASES_RISK"
    NEUTRAL = "NEUTRAL"


class PhysiologicalValidationError(ValueError):
    """Raised when an input value falls outside plausible human biological limits."""
    def __init__(self, metric: str, value: float, unit: str, allowed_min: float, allowed_max: float):
        self.metric = metric
        self.value = value
        self.unit = unit
        self.allowed_min = allowed_min
        self.allowed_max = allowed_max
        super().__init__(
            f"Physiologically implausible value for {metric}: {value} {unit}. "
            f"Expected physiological range is [{allowed_min}, {allowed_max}] {unit}."
        )


class UnitNormalizer:
    """Standardizes incoming clinical measurements into canonical units:
    - Glucose: mg/dL
    - Lipids / Cholesterol: mg/dL
    - Serum Creatinine: mg/dL
    - Height: cm
    - Weight: kg
    - Blood Pressure: mmHg
    """

    @classmethod
    def normalize(cls, metric: str, value: float, unit: Optional[str] = None) -> Tuple[float, str]:
        if not unit:
            return value, cls.get_canonical_unit(metric)

        u = unit.strip().lower()
        m = metric.upper()

        # Glucose normalization
        if m in ["FASTING_GLUCOSE", "GLUCOSE", "RANDOM_GLUCOSE"]:
            if u in ["mmol/l", "mmol"]:
                return round(value * 18.0182, 1), "mg/dL"
            return value, "mg/dL"

        # Lipids normalization
        if m in ["TOTAL_CHOLESTEROL", "LDL_CHOLESTEROL", "HDL_CHOLESTEROL", "TRIGLYCERIDES"]:
            if u in ["mmol/l", "mmol"]:
                return round(value * 38.67, 1), "mg/dL"
            return value, "mg/dL"

        # Creatinine normalization
        if m in ["SERUM_CREATININE", "CREATININE"]:
            if u in ["umol/l", "µmol/l", "micromol/l"]:
                return round(value / 88.4, 2), "mg/dL"
            return value, "mg/dL"

        # Height normalization
        if m in ["HEIGHT", "BODY_HEIGHT"]:
            if u in ["in", "inch", "inches"]:
                return round(value * 2.54, 1), "cm"
            elif u in ["m", "meter", "meters"] and value < 3.0:
                return round(value * 100.0, 1), "cm"
            return value, "cm"

        # Weight normalization
        if m in ["WEIGHT", "BODY_WEIGHT"]:
            if u in ["lb", "lbs", "pound", "pounds"]:
                return round(value / 2.20462, 1), "kg"
            return value, "kg"

        # Blood pressure normalization
        if m in ["SYSTOLIC_BP", "DIASTOLIC_BP"]:
            if u in ["kpa"]:
                return round(value * 7.50062, 1), "mmHg"
            return value, "mmHg"

        return value, unit

    @classmethod
    def get_canonical_unit(cls, metric: str) -> str:
        defaults = {
            "SYSTOLIC_BP": "mmHg",
            "DIASTOLIC_BP": "mmHg",
            "HEART_RATE": "beats/min",
            "FASTING_GLUCOSE": "mg/dL",
            "HBA1C": "%",
            "TOTAL_CHOLESTEROL": "mg/dL",
            "LDL_CHOLESTEROL": "mg/dL",
            "HDL_CHOLESTEROL": "mg/dL",
            "TRIGLYCERIDES": "mg/dL",
            "SERUM_CREATININE": "mg/dL",
            "EGFR": "mL/min/1.73m2",
            "HEIGHT": "cm",
            "WEIGHT": "kg",
            "BMI": "kg/m2",
            "WAIST_CIRCUMFERENCE": "cm",
            "SLEEP_HOURS": "hours",
            "STRESS_LEVEL": "score",
            "AGE": "years",
        }
        return defaults.get(metric.upper(), "")


class PhysiologicalRangeValidator:
    """Enforces rigorous human physiological boundaries to prevent garbage-in garbage-out."""

    PHYSIOLOGICAL_LIMITS: Dict[str, Tuple[float, float, str]] = {
        "SYSTOLIC_BP": (60.0, 280.0, "mmHg"),
        "DIASTOLIC_BP": (30.0, 160.0, "mmHg"),
        "HEART_RATE": (30.0, 240.0, "beats/min"),
        "FASTING_GLUCOSE": (35.0, 600.0, "mg/dL"),
        "HBA1C": (3.0, 20.0, "%"),
        "TOTAL_CHOLESTEROL": (50.0, 600.0, "mg/dL"),
        "LDL_CHOLESTEROL": (20.0, 450.0, "mg/dL"),
        "HDL_CHOLESTEROL": (10.0, 150.0, "mg/dL"),
        "TRIGLYCERIDES": (30.0, 1200.0, "mg/dL"),
        "SERUM_CREATININE": (0.1, 20.0, "mg/dL"),
        "EGFR": (2.0, 200.0, "mL/min/1.73m2"),
        "HEIGHT": (80.0, 250.0, "cm"),
        "WEIGHT": (20.0, 300.0, "kg"),
        "BMI": (10.0, 90.0, "kg/m2"),
        "WAIST_CIRCUMFERENCE": (40.0, 180.0, "cm"),
        "SLEEP_HOURS": (1.0, 20.0, "hours"),
        "STRESS_LEVEL": (0.0, 10.0, "score"),
        "AGE": (1.0, 125.0, "years"),
    }

    @classmethod
    def validate(cls, metric: str, value: float, unit: str):
        m = metric.upper()
        if m in cls.PHYSIOLOGICAL_LIMITS:
            min_v, max_v, canonical_u = cls.PHYSIOLOGICAL_LIMITS[m]
            if value < min_v or value > max_v:
                raise PhysiologicalValidationError(
                    metric=metric,
                    value=value,
                    unit=unit,
                    allowed_min=min_v,
                    allowed_max=max_v,
                )


class RiskFactorContribution(BaseModel):
    """Granular explainability attribution for a specific input variable."""
    feature: str
    observed_value: str
    target_value: str
    impact_weight: float                    # Normalized attribution magnitude 0.0 to 1.0
    impact_direction: ImpactDirection       # INCREASES_RISK or DECREASES_RISK
    category: str                           # BIOMETRIC | LIFESTYLE | GENETIC | DEMOGRAPHIC
    explanation: str                        # Plain-language explanation for clinician and citizen
    evidence_citation: str                  # Scientific paper, clinical guideline, or policy origin


class DomainRiskResult(BaseModel):
    """Rigorous standard output contract for every clinical risk model assessment."""
    risk_domain: str
    score: Optional[float] = None           # 0.0 to 1.0 (None when INSUFFICIENT_DATA)
    risk_category: RiskCategory
    model_version: str
    calculation_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    input_snapshot: Dict[str, Any]          # Normalized inputs with units used for calculation
    risk_factor_contributions: List[RiskFactorContribution] = []
    confidence: float                       # 0.0 to 1.0 based on data completeness & sensor quality
    missing_requirements: List[str] = Field(default_factory=list) # Essential requirements absent
    limitations: List[str]                  # Explicit boundaries, proxies used, missing factors
    recommended_next_step: str              # Actionable clinical / lifestyle directive
    is_clinically_validated: bool           # True if peer-reviewed guideline; False if heuristic/demo
    provenance: Dict[str, str] = {}         # Authors, society guidelines, DOI/URL
    clinical_safety_notice: str = (
        "SAFETY NOTICE: Risk estimation is NOT diagnosis. This score indicates statistical "
        "and guideline-based probability. It does NOT independently diagnose illness or prescribe therapy. "
        "High-risk assessments require qualified clinical evaluation."
    )


class RiskModel(ABC):
    """Abstract model contract that all SevaHealth clinical domain models must implement."""

    @property
    @abstractmethod
    def domain(self) -> str:
        """Domain name (e.g. 'Diabetes & Metabolic Risk')."""
        pass

    @abstractmethod
    def version(self) -> str:
        """Model semantic version."""
        pass

    @abstractmethod
    def provenance(self) -> Dict[str, str]:
        """Formal citation, guideline publisher, and scientific basis."""
        pass

    @abstractmethod
    def is_clinically_validated(self) -> bool:
        """Indicates whether this model implements a validated clinical guideline or a demonstration proxy."""
        pass

    @abstractmethod
    def required_inputs(self) -> List[str]:
        """List of mandatory input keys. Missing any mandatory input triggers INSUFFICIENT_DATA."""
        pass

    @abstractmethod
    def optional_inputs(self) -> List[str]:
        """List of optional inputs that enhance model confidence."""
        pass

    @abstractmethod
    def limitations(self) -> List[str]:
        """Clinical and algorithmic limitations of this model."""
        pass

    def validate_and_normalize_inputs(
        self, raw_inputs: Dict[str, Any]
    ) -> Tuple[Dict[str, float], Dict[str, Any], List[str]]:
        """Normalizes units and validates physiological limits.
        NEVER silently substitutes missing clinical values.
        
        Returns:
            clean_inputs: Map of normalized metric names to numeric values.
            metadata_inputs: Non-numeric inputs (e.g. family history, smoking category).
            missing_mandatory: List of required keys that were absent.
        """
        clean_inputs: Dict[str, float] = {}
        metadata_inputs: Dict[str, Any] = {}
        missing_mandatory: List[str] = []

        # Check mandatory inputs
        for req in self.required_inputs():
            if req not in raw_inputs or raw_inputs[req] is None:
                missing_mandatory.append(req)

        # Process all provided inputs
        for k, v in raw_inputs.items():
            if v is None:
                continue

            k_norm = k.upper()
            if isinstance(v, (int, float)):
                # Unit normalization using default canonical unit
                norm_val, norm_unit = UnitNormalizer.normalize(k_norm, float(v))
                PhysiologicalRangeValidator.validate(k_norm, norm_val, norm_unit)
                clean_inputs[k_norm] = norm_val
            elif isinstance(v, dict) and "value" in v:
                val = float(v["value"])
                u = v.get("unit")
                norm_val, norm_unit = UnitNormalizer.normalize(k_norm, val, u)
                PhysiologicalRangeValidator.validate(k_norm, norm_val, norm_unit)
                clean_inputs[k_norm] = norm_val
            else:
                metadata_inputs[k_norm] = v

        return clean_inputs, metadata_inputs, missing_mandatory

    @abstractmethod
    def calculate(self, inputs: Dict[str, Any]) -> DomainRiskResult:
        """Computes risk score, assigns RiskCategory, and formulates explainability contributions."""
        pass

    @abstractmethod
    def explain(self, inputs: Dict[str, Any], result: DomainRiskResult) -> List[RiskFactorContribution]:
        """Generates ranked explainable feature contributions."""
        pass

    @abstractmethod
    def confidence(self, inputs: Dict[str, Any]) -> float:
        """Calculates confidence score based strictly on input completeness."""
        pass

    @abstractmethod
    def recommended_next_step(self, category: RiskCategory, contributions: List[RiskFactorContribution]) -> str:
        """Formulates actionable human-in-the-loop next step."""
        pass
