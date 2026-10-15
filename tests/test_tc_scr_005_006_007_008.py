"""Automated Test Suite for Clinical Screening & Observations Suite.
Covers:
- TC-SCR-005: BMI calculation (Height = 1.70 m, weight = 70 kg -> 24.22 kg/m²)
- TC-SCR-006: Duplicate measurement ingestion (Idempotency policy enforcement)
- TC-SCR-007: Conflicting observations (Preservation & documented selection policy)
- TC-SCR-008: Historical observation preservation (Chronological order preserved)
"""

import pytest
from datetime import datetime, timezone, timedelta
from starlette.testclient import TestClient

from services.api.main import app
from services.store import store, CitizenRecord
from packages.types.enums import Gender
from packages.clinical_models.observations import Observation
from services.screening.calculator import calculate_bmi


client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_citizen():
    """Ensures test citizen exists in store before each test."""
    cid = "citizen-test-synth-scr"
    cit = CitizenRecord(
        id=cid,
        tenant_id="tenant-karnataka-moh",
        user_id="user-synth-scr-01",
        abha_id="91-4829-1029-4820",
        first_name="Ramesh",
        last_name="Patel",
        birth_date="1975-05-20",
        gender=Gender.MALE,
        phone="+919845123456",
        state="Karnataka",
        district="Bengaluru Rural",
        sub_district="Devanahalli",
        village_or_ward="Ward 12",
    )
    store.add_citizen(cit)
    # Clear observations for isolated testing
    store.observations[cid] = []
    yield cid


def test_tc_scr_005_bmi_calculation_unit():
    """TC-SCR-005 Unit Level: Formula verification (weight / height^2).
    Height = 1.70 m; weight = 70 kg -> ~24.22 kg/m²
    """
    res = calculate_bmi(height=1.70, weight=70.0, height_unit="m", weight_unit="kg", rounding_decimals=2)
    assert res["height_m"] == 1.70
    assert res["weight_kg"] == 70.0
    assert res["bmi"] == 24.22
    assert abs(res["raw_bmi"] - (70.0 / (1.70 ** 2))) < 1e-9
    assert res["unit"] == "kg/m2"
    assert "weight in kilograms ÷ (height in metres)²" in res["formula"]


def test_tc_scr_005_bmi_calculation_api_and_retrieval(setup_test_citizen):
    """TC-SCR-005 P0 Steps:
    1. Record height (1.70 m) and weight (70 kg).
    2. Trigger BMI calculation.
    3. Retrieve the stored result.
    Expected: BMI is approximately 24.22 kg/m² and persisted in database.
    """
    cid = setup_test_citizen

    # Step 1 & 2: Record & Trigger calculation
    payload = {
        "citizen_id": cid,
        "height": 1.70,
        "weight": 70.0,
        "height_unit": "m",
        "weight_unit": "kg",
        "rounding_decimals": 2,
        "store_result": True,
    }
    calc_resp = client.post("/api/v1/screening/bmi/calculate", json=payload)
    assert calc_resp.status_code == 200, calc_resp.text
    data = calc_resp.json()

    assert data["bmi"] == 24.22
    assert data["unit"] == "kg/m2"
    assert len(data["stored_observation_ids"]) == 3  # HEIGHT, WEIGHT, BMI

    # Step 3: Retrieve stored result
    get_resp = client.get(f"/api/v1/screening/bmi/{cid}")
    assert get_resp.status_code == 200, get_resp.text
    stored_data = get_resp.json()

    assert stored_data["citizen_id"] == cid
    assert stored_data["latest_bmi"]["value"] == 24.22
    assert stored_data["latest_bmi"]["code"] == "BMI"
    assert stored_data["latest_bmi"]["unit"] == "kg/m2"
    assert stored_data["total_bmi_records"] >= 1


def test_tc_scr_006_duplicate_measurement_ingestion(setup_test_citizen):
    """TC-SCR-006 P1 Steps:
    1. Import an observation with a unique source event identifier.
    2. Submit the same event again.
    3. Query the stored observations.
    Expected: Idempotency policy followed, no duplicate clinical observations created.
    """
    cid = setup_test_citizen
    event_id = "evt-vital-source-998811"
    now_ts = datetime(2026, 10, 10, 9, 30, 0, tzinfo=timezone.utc).isoformat()

    event_payload = {
        "citizen_id": cid,
        "code": "SYSTOLIC_BP",
        "value": 138.0,
        "unit": "mmHg",
        "source": "CLINICAL_DEVICE",
        "source_event_id": event_id,
        "recorded_at": now_ts,
    }

    # 1. First import
    resp1 = client.post("/api/v1/screening/observations", json=event_payload)
    assert resp1.status_code == 200, resp1.text
    res1 = resp1.json()
    assert res1["status"] == "INGESTED"
    assert res1["idempotent_replay"] is False
    assert res1["total_stored_count"] == 1

    # 2. Re-submit same event again
    resp2 = client.post("/api/v1/screening/observations", json=event_payload)
    assert resp2.status_code == 200, resp2.text
    res2 = resp2.json()
    assert res2["status"] == "DEDUPLICATED"
    assert res2["idempotent_replay"] is True
    assert res2["total_stored_count"] == 1  # Did NOT increment

    # 3. Query stored observations
    query_resp = client.get(f"/api/v1/screening/observations/{cid}?code=SYSTOLIC_BP")
    assert query_resp.status_code == 200
    stored_list = query_resp.json()

    # Confirms exactly 1 observation is stored
    assert len(stored_list) == 1
    assert stored_list[0]["source_event_id"] == event_id
    assert stored_list[0]["value"] == 138.0


def test_tc_scr_007_conflicting_observations(setup_test_citizen):
    """TC-SCR-007 P1 Steps:
    1. Enter two measurements with the same nominal time but different values and sources.
    2. Request the latest measurement summary.
    Expected: System preserves both observations and applies documented selection policy.
              Does NOT silently discard provenance.
    """
    cid = setup_test_citizen
    nominal_time = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc).isoformat()

    # Measurement A: MANUAL_ENTRY (140 mmHg)
    obs_a = {
        "citizen_id": cid,
        "code": "SYSTOLIC_BP",
        "value": 140.0,
        "unit": "mmHg",
        "source": "MANUAL_ENTRY",
        "recorded_at": nominal_time,
        "source_event_id": "evt-a-manual-01",
        "provenance": {"operator": "ASHA_Worker_Sunita", "device": "Manual Cuff"},
    }
    # Measurement B: CLINICAL_DEVICE (132 mmHg)
    obs_b = {
        "citizen_id": cid,
        "code": "SYSTOLIC_BP",
        "value": 132.0,
        "unit": "mmHg",
        "source": "CLINICAL_DEVICE",
        "recorded_at": nominal_time,
        "source_event_id": "evt-b-device-02",
        "provenance": {"operator": "PHC_Nurse", "device": "Omron_Automated_Sphygmomanometer_v3"},
    }

    client.post("/api/v1/screening/observations", json=obs_a)
    client.post("/api/v1/screening/observations", json=obs_b)

    # 1. Query full history: verify BOTH observations are preserved (neither discarded)
    history_resp = client.get(f"/api/v1/screening/observations/{cid}?code=SYSTOLIC_BP")
    assert history_resp.status_code == 200
    history = history_resp.json()
    assert len(history) == 2, "Both observations must be preserved"
    sources = {h["source"] for h in history}
    values = {h["value"] for h in history}
    assert sources == {"MANUAL_ENTRY", "CLINICAL_DEVICE"}
    assert values == {140.0, 132.0}

    # 2. Request latest measurement summary
    summary_resp = client.get(f"/api/v1/screening/observations/{cid}/summary?code=SYSTOLIC_BP")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()

    assert summary["has_conflict"] is True
    # Documented policy prioritizes CLINICAL_DEVICE over MANUAL_ENTRY
    assert summary["selected_observation"]["source"] == "CLINICAL_DEVICE"
    assert summary["selected_observation"]["value"] == 132.0
    assert summary["provenance_discarded"] is False
    assert len(summary["conflicting_observations"]) == 2


def test_tc_scr_008_historical_observation_preservation(setup_test_citizen):
    """TC-SCR-008 P0 Steps:
    1. Record a measurement at T1.
    2. Record a later measurement at T2.
    3. Retrieve the history.
    Expected: Both observations remain available in the correct chronological order.
    """
    cid = setup_test_citizen
    t1 = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 5, 8, 0, 0, tzinfo=timezone.utc)

    obs1 = {
        "citizen_id": cid,
        "code": "FASTING_GLUCOSE",
        "value": 124.0,
        "unit": "mg/dL",
        "source": "LAB_REPORT",
        "recorded_at": t1.isoformat(),
        "source_event_id": "evt-glucose-oct01",
    }
    obs2 = {
        "citizen_id": cid,
        "code": "FASTING_GLUCOSE",
        "value": 118.0,
        "unit": "mg/dL",
        "source": "LAB_REPORT",
        "recorded_at": t2.isoformat(),
        "source_event_id": "evt-glucose-oct05",
    }

    # Submit out of chronological sequence or in sequence
    client.post("/api/v1/screening/observations", json=obs1)
    client.post("/api/v1/screening/observations", json=obs2)

    # Retrieve history
    hist_resp = client.get(f"/api/v1/screening/observations/{cid}?code=FASTING_GLUCOSE")
    assert hist_resp.status_code == 200
    history = hist_resp.json()

    assert len(history) == 2
    # Chronological ordering check
    assert history[0]["recorded_at"] == t1.isoformat()
    assert history[0]["value"] == 124.0
    assert history[1]["recorded_at"] == t2.isoformat()
    assert history[1]["value"] == 118.0
