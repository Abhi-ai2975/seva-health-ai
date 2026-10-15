"""Clinical Measurement Unit Normalization Engine.

Provides standard medical unit conversion and normalization for:
1. Height (cm, m, in, ft) -> Canonical: cm
2. Weight (kg, lb, g) -> Canonical: kg
3. Blood Pressure (mmHg, kPa) -> Canonical: mmHg
4. Fasting Blood Glucose (mg/dL, mmol/L) -> Canonical: mg/dL
5. Hemoglobin A1c (%, mmol/mol) -> Canonical: %
6. Cholesterol & Lipids (mg/dL, mmol/L) -> Canonical: mg/dL
7. Serum Creatinine (mg/dL, umol/L) -> Canonical: mg/dL
8. Waist Circumference (cm, in) -> Canonical: cm
"""

import math
from typing import Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field


class NormalizedMeasurement(BaseModel):
    metric: str
    original_value: float
    original_unit: str
    normalized_value: float
    canonical_unit: str
    tolerance_applied: Optional[float] = None
    is_valid: bool = True
    clinical_note: Optional[str] = None


# Conversion constants
INCH_TO_CM = 2.54
FOOT_TO_CM = 30.48
LB_TO_KG = 0.45359237
KPA_TO_MMHG = 7.50062
GLUCOSE_MMOL_TO_MGDL = 18.0182        # 1 mmol/L = 18.0182 mg/dL
CHOLESTEROL_MMOL_TO_MGDL = 38.67      # 1 mmol/L = 38.67 mg/dL
TRIGLYCERIDES_MMOL_TO_MGDL = 88.57    # 1 mmol/L = 88.57 mg/dL
CREATININE_UMOL_TO_MGDL = 1.0 / 88.42 # 88.42 umol/L = 1.0 mg/dL


class UnitNormalizer:
    """Standardizes heterogeneous clinical measurements into canonical units
    for NCD risk assessment and longitudinal tracking.
    """

    @classmethod
    def normalize_height(cls, value: float, unit: str) -> NormalizedMeasurement:
        u = unit.strip().lower()
        canonical_unit = "cm"
        val = float(value)

        if u in ["cm", "centimeter", "centimeters"]:
            norm = val
        elif u in ["m", "meter", "meters"]:
            norm = val * 100.0
        elif u in ["in", "inch", "inches"]:
            norm = val * INCH_TO_CM
        elif u in ["ft", "feet", "foot"]:
            norm = val * FOOT_TO_CM
        else:
            raise ValueError(f"Unsupported height unit: '{unit}'. Supported: cm, m, in, ft.")

        norm = round(norm, 1)
        return NormalizedMeasurement(
            metric="HEIGHT",
            original_value=value,
            original_unit=unit,
            normalized_value=norm,
            canonical_unit=canonical_unit,
        )

    @classmethod
    def normalize_weight(cls, value: float, unit: str) -> NormalizedMeasurement:
        u = unit.strip().lower()
        canonical_unit = "kg"
        val = float(value)

        if u in ["kg", "kgs", "kilogram", "kilograms"]:
            norm = val
        elif u in ["lb", "lbs", "pound", "pounds"]:
            norm = val * LB_TO_KG
        elif u in ["g", "gm", "gram", "grams"]:
            norm = val / 1000.0
        else:
            raise ValueError(f"Unsupported weight unit: '{unit}'. Supported: kg, lb, g.")

        norm = round(norm, 1)
        return NormalizedMeasurement(
            metric="WEIGHT",
            original_value=value,
            original_unit=unit,
            normalized_value=norm,
            canonical_unit=canonical_unit,
        )

    @classmethod
    def normalize_blood_pressure(cls, value: float, unit: str, metric_subtype: str = "SYSTOLIC_BP") -> NormalizedMeasurement:
        u = unit.strip().lower()
        canonical_unit = "mmHg"
        val = float(value)

        if u in ["mmhg", "mm hg", "mm_hg"]:
            norm = val
        elif u in ["kpa", "kilopascal", "kpascal"]:
            norm = val * KPA_TO_MMHG
        else:
            raise ValueError(f"Unsupported blood pressure unit: '{unit}'. Supported: mmHg, kPa.")

        norm = round(norm, 1)
        return NormalizedMeasurement(
            metric=metric_subtype,
            original_value=value,
            original_unit=unit,
            normalized_value=norm,
            canonical_unit=canonical_unit,
        )

    @classmethod
    def normalize_glucose(cls, value: float, unit: str) -> NormalizedMeasurement:
        u = unit.strip().lower()
        canonical_unit = "mg/dL"
        val = float(value)

        if u in ["mg/dl", "mg/100ml", "mg%", "mg / dl"]:
            norm = val
        elif u in ["mmol/l", "mmol/liter", "mmol / l"]:
            norm = val * GLUCOSE_MMOL_TO_MGDL
        else:
            raise ValueError(f"Unsupported glucose unit: '{unit}'. Supported: mg/dL, mmol/L.")

        norm = round(norm, 1)
        return NormalizedMeasurement(
            metric="FASTING_GLUCOSE",
            original_value=value,
            original_unit=unit,
            normalized_value=norm,
            canonical_unit=canonical_unit,
        )

    @classmethod
    def normalize_hba1c(cls, value: float, unit: str) -> NormalizedMeasurement:
        u = unit.strip().lower()
        canonical_unit = "%"
        val = float(value)

        if u in ["%", "percent"]:
            norm = val
        elif u in ["mmol/mol", "mmol / mol"]:
            # IFCC to NGSP formula: (0.09148 * mmol/mol) + 2.152
            norm = (0.09148 * val) + 2.152
        else:
            raise ValueError(f"Unsupported HbA1c unit: '{unit}'. Supported: %, mmol/mol.")

        norm = round(norm, 1)
        return NormalizedMeasurement(
            metric="HBA1C",
            original_value=value,
            original_unit=unit,
            normalized_value=norm,
            canonical_unit=canonical_unit,
        )

    @classmethod
    def normalize_cholesterol(cls, value: float, unit: str, metric_subtype: str = "TOTAL_CHOLESTEROL") -> NormalizedMeasurement:
        u = unit.strip().lower()
        canonical_unit = "mg/dL"
        val = float(value)

        if u in ["mg/dl", "mg / dl", "mg%"]:
            norm = val
        elif u in ["mmol/l", "mmol / l"]:
            norm = val * CHOLESTEROL_MMOL_TO_MGDL
        else:
            raise ValueError(f"Unsupported cholesterol unit: '{unit}'. Supported: mg/dL, mmol/L.")

        norm = round(norm, 1)
        return NormalizedMeasurement(
            metric=metric_subtype,
            original_value=value,
            original_unit=unit,
            normalized_value=norm,
            canonical_unit=canonical_unit,
        )

    @classmethod
    def normalize_triglycerides(cls, value: float, unit: str) -> NormalizedMeasurement:
        u = unit.strip().lower()
        canonical_unit = "mg/dL"
        val = float(value)

        if u in ["mg/dl", "mg / dl"]:
            norm = val
        elif u in ["mmol/l", "mmol / l"]:
            norm = val * TRIGLYCERIDES_MMOL_TO_MGDL
        else:
            raise ValueError(f"Unsupported triglycerides unit: '{unit}'. Supported: mg/dL, mmol/L.")

        norm = round(norm, 1)
        return NormalizedMeasurement(
            metric="TRIGLYCERIDES",
            original_value=value,
            original_unit=unit,
            normalized_value=norm,
            canonical_unit=canonical_unit,
        )

    @classmethod
    def normalize_creatinine(cls, value: float, unit: str) -> NormalizedMeasurement:
        u = unit.strip().lower()
        canonical_unit = "mg/dL"
        val = float(value)

        if u in ["mg/dl", "mg / dl"]:
            norm = val
        elif u in ["umol/l", "µmol/l", "umol / l", "micromol/l"]:
            norm = val * CREATININE_UMOL_TO_MGDL
        else:
            raise ValueError(f"Unsupported serum creatinine unit: '{unit}'. Supported: mg/dL, umol/L.")

        norm = round(norm, 2)
        return NormalizedMeasurement(
            metric="SERUM_CREATININE",
            original_value=value,
            original_unit=unit,
            normalized_value=norm,
            canonical_unit=canonical_unit,
        )

    @classmethod
    def normalize_waist(cls, value: float, unit: str) -> NormalizedMeasurement:
        u = unit.strip().lower()
        canonical_unit = "cm"
        val = float(value)

        if u in ["cm", "centimeter", "centimeters"]:
            norm = val
        elif u in ["in", "inch", "inches"]:
            norm = val * INCH_TO_CM
        elif u in ["m", "meter", "meters"]:
            norm = val * 100.0
        else:
            raise ValueError(f"Unsupported waist circumference unit: '{unit}'. Supported: cm, in, m.")

        norm = round(norm, 1)
        return NormalizedMeasurement(
            metric="WAIST_CIRCUMFERENCE",
            original_value=value,
            original_unit=unit,
            normalized_value=norm,
            canonical_unit=canonical_unit,
        )

    @classmethod
    def normalize(cls, metric: str, value: float, unit: str) -> NormalizedMeasurement:
        m = metric.upper().strip()
        if m in ["HEIGHT", "BODY_HEIGHT"]:
            return cls.normalize_height(value, unit)
        elif m in ["WEIGHT", "BODY_WEIGHT"]:
            return cls.normalize_weight(value, unit)
        elif m in ["SYSTOLIC_BP", "SBP"]:
            return cls.normalize_blood_pressure(value, unit, "SYSTOLIC_BP")
        elif m in ["DIASTOLIC_BP", "DBP"]:
            return cls.normalize_blood_pressure(value, unit, "DIASTOLIC_BP")
        elif m in ["FASTING_GLUCOSE", "GLUCOSE", "FBS"]:
            return cls.normalize_glucose(value, unit)
        elif m in ["HBA1C", "GLYCATED_HEMOGLOBIN"]:
            return cls.normalize_hba1c(value, unit)
        elif m in ["TOTAL_CHOLESTEROL", "CHOLESTEROL"]:
            return cls.normalize_cholesterol(value, unit, "TOTAL_CHOLESTEROL")
        elif m in ["HDL_CHOLESTEROL", "HDL"]:
            return cls.normalize_cholesterol(value, unit, "HDL_CHOLESTEROL")
        elif m in ["LDL_CHOLESTEROL", "LDL"]:
            return cls.normalize_cholesterol(value, unit, "LDL_CHOLESTEROL")
        elif m in ["TRIGLYCERIDES"]:
            return cls.normalize_triglycerides(value, unit)
        elif m in ["SERUM_CREATININE", "CREATININE"]:
            return cls.normalize_creatinine(value, unit)
        elif m in ["WAIST_CIRCUMFERENCE", "WAIST"]:
            return cls.normalize_waist(value, unit)
        else:
            raise ValueError(f"Unknown metric type: '{metric}'.")

    @classmethod
    def are_equivalent(
        cls,
        metric: str,
        val1: float,
        unit1: str,
        val2: float,
        unit2: str,
        rounding_tolerance: float = 0.5,
    ) -> Tuple[bool, float, float]:
        """Compares two measurements in potentially different units after normalization.
        Returns (are_equivalent, norm1, norm2).
        """
        norm1 = cls.normalize(metric, val1, unit1).normalized_value
        norm2 = cls.normalize(metric, val2, unit2).normalized_value
        diff = abs(norm1 - norm2)
        return (diff <= rounding_tolerance, norm1, norm2)
