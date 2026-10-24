import pytest
from datetime import datetime, timezone, timedelta
from starlette.testclient import TestClient

from services.api.main import app
from scripts.seed_data import seed_all_demo_data
from services.trajectory.models import (
    TrajectoryTrendState,
    TrajectoryDomain,
    RiskSnapshot,
    CitizenTrajectoryReport,
)
from services.trajectory.engine import RiskTrajectoryEngine

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_seed():
    seed_all_demo_data()


# =========================================================================
# 1. CORE ENGINE UNIT TESTS
# =========================================================================

def test_trajectory_engine_all_7_domains_evaluated():
    engine = RiskTrajectoryEngine()
    now = datetime.now(timezone.utc)

    snap1 = RiskSnapshot(
        citizen_id="test-c1",
        timestamp=now - timedelta(days=90),
        domain_scores={
            "metabolic": 0.30,
            "diabetes": 0.35,
            "hypertension": 0.25,
            "cardiovascular": 0.20,
            "obesity": 0.30,
            "renal": 0.10,
            "lifestyle": 0.20,
        },
        domain_tiers={d: "MODERATE" if d in ["metabolic", "diabetes", "obesity"] else "LOW" for d in engine.MANDATORY_DOMAINS},
        key_biomarkers={"HBA1C": 5.6, "SYSTOLIC_BP": 122.0, "STEPS": 8000},
    )

    snap2 = RiskSnapshot(
        citizen_id="test-c1",
        timestamp=now,
        domain_scores={
            "metabolic": 0.50,
            "diabetes": 0.60,
            "hypertension": 0.45,
            "cardiovascular": 0.35,
            "obesity": 0.55,
            "renal": 0.20,
            "lifestyle": 0.45,
        },
        domain_tiers={d: "HIGH" if d in ["metabolic", "diabetes", "obesity"] else "MODERATE" for d in engine.MANDATORY_DOMAINS},
        key_biomarkers={"HBA1C": 6.2, "SYSTOLIC_BP": 136.0, "STEPS": 5000},
    )

    report = engine.evaluate_trajectory("test-c1", [snap1, snap2])

    assert report.total_snapshots == 2
    assert set(report.domain_trajectories.keys()) == {
        "metabolic", "diabetes", "hypertension", "cardiovascular", "obesity", "renal", "lifestyle"
    }
    assert report.overall_trend == TrajectoryTrendState.WORSENING


def test_trajectory_trend_states_detection():
    engine = RiskTrajectoryEngine()
    now = datetime.now(timezone.utc)

    # 1. WORSENING Case (Score increases by >= 0.04)
    worsening_snap_base = RiskSnapshot(
        citizen_id="test-w",
        timestamp=now - timedelta(days=60),
        domain_scores={"diabetes": 0.40},
        domain_tiers={"diabetes": "MODERATE"},
        key_biomarkers={"HBA1C": 5.8},
    )
    worsening_snap_curr = RiskSnapshot(
        citizen_id="test-w",
        timestamp=now,
        domain_scores={"diabetes": 0.65},
        domain_tiers={"diabetes": "HIGH"},
        key_biomarkers={"HBA1C": 6.4},
    )
    rep_w = engine.evaluate_trajectory("test-w", [worsening_snap_base, worsening_snap_curr])
    assert rep_w.domain_trajectories["diabetes"].trend == TrajectoryTrendState.WORSENING

    # 2. IMPROVING Case (Score decreases by >= 0.04)
    improving_snap_base = RiskSnapshot(
        citizen_id="test-i",
        timestamp=now - timedelta(days=60),
        domain_scores={"hypertension": 0.70},
        domain_tiers={"hypertension": "HIGH"},
        key_biomarkers={"SYSTOLIC_BP": 150.0},
    )
    improving_snap_curr = RiskSnapshot(
        citizen_id="test-i",
        timestamp=now,
        domain_scores={"hypertension": 0.35},
        domain_tiers={"hypertension": "MODERATE"},
        key_biomarkers={"SYSTOLIC_BP": 124.0},
    )
    rep_i = engine.evaluate_trajectory("test-i", [improving_snap_base, improving_snap_curr])
    assert rep_i.domain_trajectories["hypertension"].trend == TrajectoryTrendState.IMPROVING

    # 3. STABLE Case (Score delta within 0.04)
    stable_snap_base = RiskSnapshot(
        citizen_id="test-s",
        timestamp=now - timedelta(days=60),
        domain_scores={"cardiovascular": 0.22},
        domain_tiers={"cardiovascular": "LOW"},
        key_biomarkers={"SYSTOLIC_BP": 118.0},
    )
    stable_snap_curr = RiskSnapshot(
        citizen_id="test-s",
        timestamp=now,
        domain_scores={"cardiovascular": 0.23},
        domain_tiers={"cardiovascular": "LOW"},
        key_biomarkers={"SYSTOLIC_BP": 120.0},
    )
    rep_s = engine.evaluate_trajectory("test-s", [stable_snap_base, stable_snap_curr])
    assert rep_s.domain_trajectories["cardiovascular"].trend == TrajectoryTrendState.STABLE

    # 4. INSUFFICIENT_DATA Case (Metric missing)
    insuf_snap = RiskSnapshot(
        citizen_id="test-insuf",
        timestamp=now,
        domain_scores={"renal": None},
        domain_tiers={"renal": "INSUFFICIENT_DATA"},
    )
    rep_insuf = engine.evaluate_trajectory("test-insuf", [insuf_snap])
    assert rep_insuf.domain_trajectories["renal"].trend == TrajectoryTrendState.INSUFFICIENT_DATA


def test_mathematical_change_percentage_calculation():
    engine = RiskTrajectoryEngine()
    now = datetime.now(timezone.utc)

    # Baseline 0.40 -> Current 0.60 => +50.0% change
    snap1 = RiskSnapshot(
        citizen_id="test-calc",
        timestamp=now - timedelta(days=90),
        domain_scores={"metabolic": 0.40},
        domain_tiers={"metabolic": "MODERATE"},
    )
    snap2 = RiskSnapshot(
        citizen_id="test-calc",
        timestamp=now,
        domain_scores={"metabolic": 0.60},
        domain_tiers={"metabolic": "HIGH"},
    )

    report = engine.evaluate_trajectory("test-calc", [snap1, snap2])
    comp = report.domain_trajectories["metabolic"]
    assert comp.change_percentage_from_baseline == 50.0
    assert comp.absolute_delta_from_baseline == 0.20
    assert comp.absolute_delta_from_previous == 0.20


# =========================================================================
# 2. LINGUISTIC NON-CAUSALITY PHRASING VERIFICATION
# =========================================================================

def test_strictly_non_causal_phrasing_compliance():
    """Verify that contributing factors NEVER say 'caused by' and use
    'associated with', 'contributing factor', or 'may be contributing'.
    """
    engine = RiskTrajectoryEngine()
    now = datetime.now(timezone.utc)

    snap1 = RiskSnapshot(
        citizen_id="test-lang",
        timestamp=now - timedelta(days=120),
        domain_scores={"diabetes": 0.35, "hypertension": 0.30, "lifestyle": 0.25},
        domain_tiers={"diabetes": "MODERATE", "hypertension": "MODERATE", "lifestyle": "LOW"},
        key_biomarkers={"HBA1C": 5.6, "FASTING_GLUCOSE": 98.0, "SYSTOLIC_BP": 120.0, "STEPS": 8500},
    )
    snap2 = RiskSnapshot(
        citizen_id="test-lang",
        timestamp=now,
        domain_scores={"diabetes": 0.68, "hypertension": 0.60, "lifestyle": 0.55},
        domain_tiers={"diabetes": "HIGH", "hypertension": "HIGH", "lifestyle": "HIGH"},
        key_biomarkers={"HBA1C": 6.4, "FASTING_GLUCOSE": 122.0, "SYSTOLIC_BP": 142.0, "STEPS": 4200},
    )

    report = engine.evaluate_trajectory("test-lang", [snap1, snap2])

    for dom, comp in report.domain_trajectories.items():
        for factor in comp.contributing_factors:
            # Rule 1: NEVER infer direct causality
            assert "caused by" not in factor.lower(), f"Forbidden phrasing 'caused by' detected in: {factor}"
            # Rule 2: Must utilize probabilistic / associative phrasing
            if comp.trend != TrajectoryTrendState.INSUFFICIENT_DATA:
                assert any(
                    phrase in factor.lower()
                    for phrase in ["associated with", "contributing factor", "may be contributing", "correlated with", "remains consistent"]
                ), f"Missing approved associative language in: {factor}"


# =========================================================================
# 3. VISUAL TRAJECTORY GENERATION TESTS
# =========================================================================

def test_visual_html_generation():
    engine = RiskTrajectoryEngine()
    now = datetime.now(timezone.utc)
    snaps = [
        RiskSnapshot(citizen_id="c1", timestamp=now - timedelta(days=60), domain_scores={"diabetes": 0.40, "hypertension": 0.30}),
        RiskSnapshot(citizen_id="c1", timestamp=now, domain_scores={"diabetes": 0.60, "hypertension": 0.50}),
    ]
    report = engine.evaluate_trajectory("c1", snaps)
    html = engine.generate_visual_html(report, citizen_name="Ramesh Patel")

    assert "<!DOCTYPE html>" in html
    assert "https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js" in html
    assert "Ramesh Patel" in html
    assert "Longitudinal Risk Trajectory" in html
    assert "<svg" in html


def test_visual_svg_generation():
    engine = RiskTrajectoryEngine()
    now = datetime.now(timezone.utc)
    snaps = [
        RiskSnapshot(citizen_id="c1", timestamp=now - timedelta(days=60), domain_scores={"diabetes": 0.40, "hypertension": 0.30}),
        RiskSnapshot(citizen_id="c1", timestamp=now, domain_scores={"diabetes": 0.60, "hypertension": 0.50}),
    ]
    report = engine.evaluate_trajectory("c1", snaps)
    svg = engine.generate_visual_svg(report)

    assert "<svg" in svg
    assert "</svg>" in svg
    assert "<path" in svg or "<circle" in svg


# =========================================================================
# 4. API GATEWAY ENDPOINT TESTS
# =========================================================================

@pytest.fixture
def auth_headers():
    auth_res = client.post(
        "/api/v1/auth/token",
        json={"email": "doctor@sevahealth.ai", "password": "password123"}
    )
    token = auth_res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_api_get_citizen_trajectory_ramesh_patel(auth_headers):
    # Ramesh has 3 seeded snapshots demonstrating WORSENING trajectory
    response = client.get("/api/v1/trajectory/citizen-ramesh-patel-01", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert data["citizen_id"] == "citizen-ramesh-patel-01"
    assert data["total_snapshots"] >= 3
    assert data["overall_trend"] == "WORSENING"
    assert "domain_trajectories" in data
    assert "metabolic" in data["domain_trajectories"]
    assert "diabetes" in data["domain_trajectories"]
    assert data["domain_trajectories"]["diabetes"]["trend"] == "WORSENING"
    assert data["domain_trajectories"]["diabetes"]["change_percentage_from_baseline"] is not None


def test_api_get_domain_trajectory(auth_headers):
    response = client.get("/api/v1/trajectory/citizen-ramesh-patel-01/domain/diabetes", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert data["domain"] == "diabetes"
    assert data["current_tier"] == "HIGH"
    assert data["baseline_tier"] == "MODERATE"
    assert data["trend"] == "WORSENING"
    assert len(data["contributing_factors"]) > 0


def test_api_get_domain_trajectory_invalid_domain_400(auth_headers):
    response = client.get("/api/v1/trajectory/citizen-ramesh-patel-01/domain/invalid_domain_xyz", headers=auth_headers)
    assert response.status_code == 400


def test_api_record_trajectory_snapshot(auth_headers):
    now = datetime.now(timezone.utc)
    new_snapshot_payload = {
        "citizen_id": "citizen-priya-sharma-04",
        "timestamp": now.isoformat(),
        "domain_scores": {
            "metabolic": 0.12,
            "diabetes": 0.09,
            "hypertension": 0.10,
            "cardiovascular": 0.08,
            "obesity": 0.11,
            "renal": 0.05,
            "lifestyle": 0.07,
        },
        "domain_tiers": {
            "metabolic": "LOW",
            "diabetes": "LOW",
            "hypertension": "LOW",
            "cardiovascular": "LOW",
            "obesity": "LOW",
            "renal": "LOW",
            "lifestyle": "LOW",
        },
        "key_biomarkers": {
            "HBA1C": 5.0,
            "SYSTOLIC_BP": 110.0,
            "STEPS": 10500,
        },
        "source": "CLINICAL_CHECKUP",
    }
    response = client.post("/api/v1/trajectory/citizen-priya-sharma-04/snapshot", json=new_snapshot_payload, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total_snapshots"] >= 3


def test_api_get_visual_html(auth_headers):
    response = client.get("/api/v1/trajectory/citizen-ramesh-patel-01/visual", headers=auth_headers)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Longitudinal Risk Trajectory: Ramesh Patel" in response.text


def test_api_get_visual_svg(auth_headers):
    response = client.get("/api/v1/trajectory/citizen-ramesh-patel-01/svg", headers=auth_headers)
    assert response.status_code == 200
    assert "image/svg+xml" in response.headers["content-type"]
    assert "<svg" in response.text
