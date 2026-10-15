import pytest
from starlette.testclient import TestClient

from services.api.main import app
from scripts.seed_data import seed_all_demo_data

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_data():
    seed_all_demo_data()


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "HEALTHY"
    assert data["components"]["risk_engine"] == "ONLINE"


def test_login_and_token():
    response = client.post(
        "/api/v1/auth/token",
        json={"email": "doctor@sevahealth.ai", "password": "password123"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["role"] == "CLINICIAN"


def test_get_citizen():
    response = client.get("/api/v1/citizens/citizen-ramesh-patel-01")
    assert response.status_code == 200
    data = response.json()
    assert data["first_name"] == "Ramesh"
    assert data["district"] == "Bengaluru Rural"


def test_risk_evaluation():
    # Login as clinician
    auth_res = client.post(
        "/api/v1/auth/token",
        json={"email": "doctor@sevahealth.ai", "password": "password123"}
    )
    token = auth_res.json()["access_token"]
    response = client.post(
        "/api/v1/risk/evaluate/citizen-ramesh-patel-01",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["overall_tier"] in ["HIGH", "CRITICAL"]
    assert "safety_disclaimer" in data
    assert "CLINICAL REVIEW RECOMMENDED" in data["safety_disclaimer"]
    assert len(data["top_drivers"]) > 0


def test_care_plan():
    response = client.get("/api/v1/intervention/plan/citizen-ramesh-patel-01")
    assert response.status_code == 200
    data = response.json()
    assert len(data["daily_tasks"]) == 30
    assert "adherence_percentage" in data


def test_triage_queue():
    # As clinician or dev
    response = client.get("/api/v1/clinician/triage")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    # Check Lakshmi Devi is in triage
    assert any("Lakshmi" in c["citizen_name"] for c in data)


def test_population_metrics():
    response = client.get("/api/v1/population/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "Karnataka"


def test_outcome_delta():
    response = client.get("/api/v1/population/outcome-delta")
    assert response.status_code == 200
    data = response.json()
    assert "cohorts" in data
    assert data["cohorts"]["high_adherence_group"]["mean_systolic_bp_change_mmhg"] < 0


def test_risk_models_catalog():
    auth_res = client.post(
        "/api/v1/auth/token",
        json={"email": "doctor@sevahealth.ai", "password": "password123"}
    )
    token = auth_res.json()["access_token"]
    response = client.get("/api/v1/risk/models", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 5
    domains = [m["key"] for m in data]
    assert "diabetes" in domains
    assert "hypertension" in domains
    assert "cardiovascular" in domains


def test_modular_risk_evaluation():
    response = client.post("/api/v1/risk/evaluate-modular/citizen-ramesh-patel-01")
    assert response.status_code == 200
    data = response.json()
    assert data["overall_category"] in ["MODERATE", "HIGH"]
    assert "domain_results" in data
    assert "diabetes" in data["domain_results"]
    assert "hypertension" in data["domain_results"]
    assert "clinical_safety_notice" in data


def test_custom_risk_payload_evaluation():
    payload = {
        "AGE": 60,
        "SEX": "MALE",
        "SYSTOLIC_BP": 162.0,
        "DIASTOLIC_BP": 102.0,
        "HBA1C": 8.2,
        "FASTING_GLUCOSE": 165.0,
        "WAIST_CIRCUMFERENCE": 102.0,
        "BMI": 28.5,
        "SMOKING": True,
    }
    response = client.post("/api/v1/risk/evaluate-custom", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["overall_category"] == "HIGH"
    assert data["overall_score"] >= 0.60
    assert len(data["top_drivers"]) >= 3
