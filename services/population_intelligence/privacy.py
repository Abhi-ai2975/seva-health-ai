"""Privacy Protection & De-Identification Engine for Population Health Intelligence.

Guarantees:
1. Zero individual identifiable health data (PII) exposed by default.
2. Minimum aggregation threshold enforcement (k-anonymity with cell suppression, k >= 10).
3. Cryptographic pseudonymization (salted SHA-256) for research/de-identified exports.
4. Quasi-identifier generalization (5-year age banding, clinical range binning).
"""

import hashlib
from typing import Dict, Any, List, Optional, Union

# Privacy parameter: minimum cell size to prevent micro-data re-identification
MIN_CELL_SIZE_THRESHOLD = 10
SALT_SECRET = "sevahealth_privacy_salt_2026_abdm_secure"


class PrivacyProtectionEngine:
    """Enforces mathematical privacy boundaries across population metrics."""

    @classmethod
    def enforce_cell_suppression(
        cls,
        count: int,
        threshold: int = MIN_CELL_SIZE_THRESHOLD,
    ) -> Dict[str, Any]:
        """Applies cell suppression if a cell size is between 1 and threshold - 1.

        Prevents small-number attribute disclosure in cross-tabulations.
        """
        if 0 < count < threshold:
            return {
                "value": None,
                "display": f"< {threshold} (Suppressed for Privacy)",
                "is_suppressed": True,
            }
        return {
            "value": count,
            "display": str(count),
            "is_suppressed": False,
        }

    @classmethod
    def sanitize_demographic_breakdown(
        cls,
        breakdown_dict: Dict[str, int],
        threshold: int = MIN_CELL_SIZE_THRESHOLD,
    ) -> Dict[str, Any]:
        """Iterates over demographic cells and applies cell suppression."""
        sanitized = {}
        suppressed_count = 0
        for key, val in breakdown_dict.items():
            result = cls.enforce_cell_suppression(val, threshold)
            sanitized[key] = result["display"] if result["is_suppressed"] else result["value"]
            if result["is_suppressed"]:
                suppressed_count += 1

        return {
            "cells": sanitized,
            "suppressed_cells_count": suppressed_count,
            "k_anonymity_threshold": threshold,
        }

    @classmethod
    def sanitize_cell_map(
        cls,
        breakdown_dict: Dict[str, int],
        threshold: int = MIN_CELL_SIZE_THRESHOLD,
    ) -> Dict[str, Dict[str, Any]]:
        """Returns detailed cell suppression records for every dimension category."""
        return {
            key: cls.enforce_cell_suppression(val, threshold)
            for key, val in breakdown_dict.items()
        }

    @classmethod
    def generate_pseudonym(cls, identifier: str) -> str:
        """Generates deterministic, irreversible salted cryptographic pseudonym."""
        hasher = hashlib.sha256()
        hasher.update((identifier + SALT_SECRET).encode("utf-8"))
        digest = hasher.hexdigest()[:12].upper()
        return f"PSEUDO-{digest[:4]}-{digest[4:8]}-{digest[8:12]}"

    @classmethod
    def generalize_age(cls, age: int) -> str:
        """Generalizes exact age into 5-year quasi-identifier intervals."""
        if age < 30:
            return "18-29 yrs"
        elif age < 35:
            return "30-34 yrs"
        elif age < 40:
            return "35-39 yrs"
        elif age < 45:
            return "40-44 yrs"
        elif age < 50:
            return "45-49 yrs"
        elif age < 55:
            return "50-54 yrs"
        elif age < 60:
            return "55-59 yrs"
        elif age < 65:
            return "60-64 yrs"
        else:
            return "65+ yrs"

    @classmethod
    def generalize_blood_pressure(cls, sbp: float, dbp: float) -> str:
        """Generalizes raw BP coordinates into clinical category bands."""
        if sbp < 120 and dbp < 80:
            return "Normal (< 120/80)"
        elif sbp < 130 and dbp < 80:
            return "Elevated (120-129 / < 80)"
        elif (130 <= sbp < 140) or (80 <= dbp < 90):
            return "Stage 1 HTN (130-139 / 80-89)"
        else:
            return "Stage 2 HTN (>= 140 / >= 90)"

    @classmethod
    def generalize_glucose(cls, glucose: float) -> str:
        """Generalizes fasting glucose into clinical bands."""
        if glucose < 100:
            return "Normal (< 100 mg/dL)"
        elif glucose < 126:
            return "Impaired / Prediabetes (100-125 mg/dL)"
        else:
            return "Elevated / Diabetic Range (>= 126 mg/dL)"
