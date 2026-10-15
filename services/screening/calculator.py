from packages.clinical_models.screening import IDRSSurvey, CBACSurvey


def calculate_idrs(survey: IDRSSurvey) -> int:
    """Calculates Indian Diabetes Risk Score (0 - 100 points).
    < 30: Low Risk | 30 - 50: Medium Risk | >= 60: High Risk
    """
    score = 0

    # Age criteria
    if survey.age_category == ">=50":
        score += 30
    elif survey.age_category == "35-49":
        score += 20
    else:
        score += 0

    # Waist category
    if ">=100" in survey.waist_category or ">=90" in survey.waist_category:
        score += 20
    elif "90-99" in survey.waist_category or "80-89" in survey.waist_category:
        score += 10
    else:
        score += 0

    # Physical activity
    if survey.physical_activity == "None":
        score += 30
    elif survey.physical_activity == "Sedentary":
        score += 20
    elif survey.physical_activity == "Moderate":
        score += 10
    else:
        score += 0

    # Family history
    if survey.family_history == "Both parents":
        score += 20
    elif survey.family_history == "One parent":
        score += 10
    else:
        score += 0

    return score


def calculate_cbac(survey: CBACSurvey) -> int:
    """Calculates CBAC Score (0 - 10 points). Score >= 4 recommends PHC referral."""
    score = 0
    if survey.age_over_30:
        score += 2
    if survey.tobacco_user:
        score += 2
    if survey.alcohol_consumption:
        score += 1
    if survey.waist_circumference_exceeded:
        score += 2
    if survey.physical_activity_below_150min:
        score += 2
    if survey.family_history_diabetes_or_htn:
        score += 1
    return score


def calculate_bmi(
    height: float,
    weight: float,
    height_unit: str = "m",
    weight_unit: str = "kg",
    rounding_decimals: int = 2
) -> dict:
    """Calculates Body Mass Index (BMI) following standard clinical guidelines (TC-SCR-005).
    Formula: BMI = weight in kilograms ÷ (height in metres)².

    Expected result for height=1.70 m, weight=70 kg is approximately 24.22 kg/m².
    """
    if height <= 0:
        raise ValueError(f"Invalid height ({height}): must be strictly positive non-zero value.")
    if weight <= 0:
        raise ValueError(f"Invalid weight ({weight}): must be strictly positive non-zero value.")

    # Normalize height to metres
    h_unit_norm = height_unit.lower().strip()
    if h_unit_norm in ["cm", "centimeter", "centimeters"]:
        height_m = height / 100.0
    elif h_unit_norm in ["m", "meter", "meters", "metre", "metres"]:
        height_m = height
    elif h_unit_norm in ["in", "inch", "inches"]:
        height_m = height * 0.0254
    elif h_unit_norm in ["ft", "feet", "foot"]:
        height_m = height * 0.3048
    else:
        # Heuristic: if height > 3.0, treat as cm, otherwise meters
        height_m = height / 100.0 if height > 3.0 else height

    # Normalize weight to kilograms
    w_unit_norm = weight_unit.lower().strip()
    if w_unit_norm in ["kg", "kilogram", "kilograms"]:
        weight_kg = weight
    elif w_unit_norm in ["lb", "lbs", "pound", "pounds"]:
        weight_kg = weight * 0.45359237
    elif w_unit_norm in ["g", "gram", "grams"]:
        weight_kg = weight / 1000.0
    else:
        weight_kg = weight

    if height_m <= 0:
        raise ValueError("Normalized height in metres must be greater than zero.")

    raw_bmi = weight_kg / (height_m ** 2)
    rounded_bmi = round(raw_bmi, rounding_decimals)

    # South Asian / ICMR-INDIAB criteria
    if rounded_bmi < 18.5:
        category = "UNDERWEIGHT"
        risk_tier = "MALNUTRITION_RISK"
    elif rounded_bmi < 23.0:
        category = "NORMAL"
        risk_tier = "HEALTHY_RANGE"
    elif rounded_bmi < 25.0:
        category = "OVERWEIGHT"
        risk_tier = "MODERATE_RISK"
    else:
        category = "OBESE"
        risk_tier = "HIGH_RISK"

    return {
        "height_input": height,
        "height_unit": height_unit,
        "height_m": round(height_m, 4),
        "weight_input": weight,
        "weight_unit": weight_unit,
        "weight_kg": round(weight_kg, 2),
        "bmi": rounded_bmi,
        "raw_bmi": raw_bmi,
        "unit": "kg/m2",
        "category": category,
        "risk_tier": risk_tier,
        "rounding_rule": f"Half-up rounded to {rounding_decimals} decimal places",
        "formula": "weight in kilograms ÷ (height in metres)²",
    }
