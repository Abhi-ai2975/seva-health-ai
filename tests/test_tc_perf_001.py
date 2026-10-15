"""Section 16: Test Cases — Performance and Reliability.

TC-PERF-001: Risk-engine response time (Priority: P1)

Steps:
1. Run the defined assessment workload.
2. Collect latency percentiles (p50, p90, p95, p99, min, max, avg).
3. Compare them with the approved test target.
Expected result:
The p95 latency meets the agreed target for the defined workload,
initially proposed as no more than two seconds (2000 ms) for routine deterministic assessments.
Verify database persistence and data integrity.
"""

import time
import math
import statistics
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
from starlette.testclient import TestClient

from services.api.main import app
from services.risk_engine.engine import ncd_risk_engine
from services.store import store
from packages.clinical_models.risk import RiskAssessment, DomainRiskScores
from packages.types.enums import RiskTier, TrajectoryTrend
from packages.observability.metrics import metrics

client = TestClient(app)


# ==============================================================================
# TC-PERF-001: TEST SUITE
# ==============================================================================

@pytest.mark.asyncio
async def test_tc_perf_001_routine_assessment_workload_latency_percentiles():
    """TC-PERF-001: Executes 50-assessment workload, collects percentiles, verifies p95 <= 2.0s and DB persistence."""
    workload_size = 50
    approved_target_seconds = 2.0
    approved_target_ms = approved_target_seconds * 1000.0  # 2000.0 ms

    # Realistic patient phenotype templates
    cohort_templates = [
        {"age": 45, "sex": "MALE", "sbp": 138.0, "dbp": 88.0, "fbg": 118.0, "hba1c": 6.2, "bmi": 27.2, "waist": 94.0, "smoke": False, "tc": 210.0, "hdl": 42.0},
        {"age": 58, "sex": "FEMALE", "sbp": 152.0, "dbp": 94.0, "fbg": 142.0, "hba1c": 7.4, "bmi": 29.5, "waist": 91.0, "smoke": False, "tc": 235.0, "hdl": 38.0},
        {"age": 34, "sex": "MALE", "sbp": 122.0, "dbp": 78.0, "fbg": 92.0, "hba1c": 5.4, "bmi": 22.8, "waist": 82.0, "smoke": True, "tc": 178.0, "hdl": 50.0},
        {"age": 63, "sex": "MALE", "sbp": 164.0, "dbp": 98.0, "fbg": 168.0, "hba1c": 8.1, "bmi": 31.0, "waist": 102.0, "smoke": True, "tc": 250.0, "hdl": 35.0},
        {"age": 50, "sex": "FEMALE", "sbp": 130.0, "dbp": 84.0, "fbg": 104.0, "hba1c": 5.8, "bmi": 24.5, "waist": 85.0, "smoke": False, "tc": 195.0, "hdl": 48.0},
    ]

    latencies_ms = []
    persisted_records = []

    # Step 1: Run defined assessment workload
    for i in range(workload_size):
        tpl = cohort_templates[i % len(cohort_templates)]
        v = (i % 7) - 3

        raw_inputs = {
            "AGE": tpl["age"] + v,
            "SEX": tpl["sex"],
            "SYSTOLIC_BP": tpl["sbp"] + float(v),
            "DIASTOLIC_BP": tpl["dbp"] + float(v // 2),
            "FASTING_BLOOD_GLUCOSE": tpl["fbg"] + float(v * 2),
            "HBA1C": round(tpl["hba1c"] + (v * 0.1), 1),
            "BMI": round(tpl["bmi"] + (v * 0.2), 1),
            "WAIST_CIRCUMFERENCE": tpl["waist"] + float(v),
            "TOTAL_CHOLESTEROL": tpl["tc"],
            "HDL_CHOLESTEROL": tpl["hdl"],
            "SMOKING": tpl["smoke"],
            "IDRS_SCORE": 50 if (tpl["age"] + v) > 40 else 30,
        }

        # Measure high-precision execution latency
        t0 = time.perf_counter()
        mod_result = ncd_risk_engine.evaluate(raw_inputs=raw_inputs)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(elapsed_ms)

        # Record metrics telemetry
        tier_val = mod_result.overall_category.value
        metrics.record_risk_engine(domain="multidomain", duration_ms=elapsed_ms, risk_tier=tier_val)

        # Database persistence
        cid = f"synth-perf-citizen-{i:03d}"
        assessment = RiskAssessment(
            tenant_id="karnataka_state_health",
            citizen_id=cid,
            model_name="NCD_COMPOSITE_RISK_ENGINE",
            model_version=ncd_risk_engine.engine_version,
            input_snapshot=raw_inputs,
            overall_score=mod_result.overall_score,
            overall_tier=RiskTier(tier_val),
            domains=DomainRiskScores(
                diabetes_risk=0.45,
                hypertension_risk=0.40,
                cardiovascular_risk=0.35,
                metabolic_syndrome_risk=0.35,
                ckd_risk=0.20,
                fatty_liver_risk=0.25,
            ),
            trajectory=TrajectoryTrend.STABLE,
            confidence_score=0.95,
            top_drivers=[],
            protective_factors=[],
            clinical_summary=f"Workload assessment for {cid}.",
            evaluated_at=datetime.now(timezone.utc),
        )
        store.add_risk_assessment(assessment)
        persisted_records.append(assessment)

    # Step 2: Collect latency percentiles
    sorted_lats = sorted(latencies_ms)
    n = len(sorted_lats)
    assert n == workload_size

    def percentile(p: float) -> float:
        idx = min(int(math.ceil(p * n)) - 1, n - 1)
        return round(sorted_lats[max(0, idx)], 2)

    p50 = percentile(0.50)
    p90 = percentile(0.90)
    p95 = percentile(0.95)
    p99 = percentile(0.99)
    min_lat = round(sorted_lats[0], 2)
    max_lat = round(sorted_lats[-1], 2)
    avg_lat = round(statistics.mean(sorted_lats), 2)

    print(f"\n[TC-PERF-001 Workload Benchmark] N={n}")
    print(f"  Min: {min_lat:.2f}ms | Avg: {avg_lat:.2f}ms")
    print(f"  p50: {p50:.2f}ms | p90: {p90:.2f}ms | p95: {p95:.2f}ms | p99: {p99:.2f}ms | Max: {max_lat:.2f}ms")
    print(f"  Approved Target: p95 <= {approved_target_ms:.0f}ms ({approved_target_seconds:.1f}s)")

    # Step 3: Compare with approved test target
    assert p95 <= approved_target_ms, f"p95 latency ({p95:.2f}ms) exceeded approved target ({approved_target_ms:.2f}ms)!"
    assert avg_lat < 150.0, f"Average latency ({avg_lat:.2f}ms) unexpectedly high!"

    # Database Verification: Verify all 50 assessments are stored and retrievable
    for i, a in enumerate(persisted_records):
        cid = f"synth-perf-citizen-{i:03d}"
        stored_assessment = store.get_latest_risk(cid)
        assert stored_assessment is not None, f"Database persistence missing for {cid}"
        assert stored_assessment.id == a.id
        assert stored_assessment.model_name == "NCD_COMPOSITE_RISK_ENGINE"
        assert stored_assessment.model_version == ncd_risk_engine.engine_version
        assert stored_assessment.input_snapshot["SEX"] in ["MALE", "FEMALE"]


def test_tc_perf_001_api_workload_benchmark_endpoint():
    """TC-PERF-001: Validates the POST /api/v1/risk/benchmark/workload endpoint."""
    resp = client.post("/api/v1/risk/benchmark/workload?workload_size=50")
    assert resp.status_code == 200, f"Benchmark endpoint failed: {resp.text}"
    data = resp.json()

    assert data["test_case"] == "TC-PERF-001"
    assert data["workload_size"] == 50
    assert data["successful_assessments"] == 50
    assert data["failed_assessments"] == 0

    percentiles = data["percentiles_ms"]
    assert "p50" in percentiles
    assert "p90" in percentiles
    assert "p95" in percentiles
    assert "p99" in percentiles

    # Target comparison
    assert data["approved_target_seconds"] == 2.0
    assert data["approved_target_ms"] == 2000.0
    assert data["p95_meets_target"] is True
    assert data["status"] == "PASSED"

    # Database verification
    db_verify = data["database_verification"]
    assert db_verify["persisted_records_count"] == 50
    assert db_verify["table"] == "risk_assessments"
    assert db_verify["db_query_recorded"] is True
    assert db_verify["sample_assessment_id"] is not None


def test_tc_perf_001_live_performance_percentiles_endpoint():
    """TC-PERF-001: Validates the GET /api/v1/risk/performance/percentiles telemetry endpoint."""
    resp = client.get("/api/v1/risk/performance/percentiles")
    assert resp.status_code == 200
    data = resp.json()

    assert data["test_case"] == "TC-PERF-001"
    assert data["metric"] == "ncd_risk_engine_latency"
    assert data["approved_target_seconds"] == 2.0
    assert data["sla_status"] == "COMPLIANT"
    assert data["p95_ms"] <= 2000.0


def test_tc_perf_001_concurrent_workload_latency_under_sla():
    """TC-PERF-001: Simulates concurrent multi-threaded workload and verifies p95 latency <= 2.0s."""
    raw_template = {
        "AGE": 52,
        "SEX": "MALE",
        "SYSTOLIC_BP": 140.0,
        "DIASTOLIC_BP": 90.0,
        "FASTING_BLOOD_GLUCOSE": 120.0,
        "HBA1C": 6.5,
        "BMI": 27.5,
        "WAIST_CIRCUMFERENCE": 92.0,
        "TOTAL_CHOLESTEROL": 200.0,
        "HDL_CHOLESTEROL": 45.0,
        "SMOKING": False,
        "IDRS_SCORE": 40,
    }

    latencies = []

    def evaluate_task(idx: int) -> float:
        inputs = dict(raw_template)
        inputs["AGE"] = 40 + (idx % 20)
        t0 = time.perf_counter()
        res = ncd_risk_engine.evaluate(raw_inputs=inputs)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert res.overall_category is not None
        return elapsed_ms

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(evaluate_task, range(30)))
        latencies.extend(results)

    sorted_lats = sorted(latencies)
    p95 = sorted_lats[int(len(sorted_lats) * 0.95)]

    print(f"\n[Concurrent Latency Benchmark] Total: {len(latencies)}, p95: {p95:.2f}ms")
    assert p95 <= 2000.0, f"Concurrent p95 latency exceeded 2.0s SLA target: {p95:.2f}ms"


def test_tc_perf_001_frontend_ui_and_database_verification():
    """TC-PERF-001: Verifies frontend UI elements in index.html and copilot_dashboard.html,
    and conducts end-to-end database audit verification of persisted risk assessments.
    """
    from pathlib import Path

    # 1. Verify static UI elements in services/api/static/index.html
    index_html_path = Path("services/api/static/index.html")
    assert index_html_path.exists(), "index.html not found!"
    index_html = index_html_path.read_text(encoding="utf-8")

    assert "openPerformanceModal()" in index_html
    assert 'id="performance-sla-modal"' in index_html
    assert 'id="perf-workload-select"' in index_html
    assert 'id="btn-run-perf-benchmark"' in index_html
    assert 'id="perf-kpi-min"' in index_html
    assert 'id="perf-kpi-p50"' in index_html
    assert 'id="perf-kpi-p90"' in index_html
    assert 'id="perf-kpi-p95"' in index_html
    assert 'id="perf-kpi-p99"' in index_html
    assert 'id="perf-kpi-max"' in index_html
    assert 'id="perf-kpi-avg"' in index_html
    assert 'id="perf-db-status-badge"' in index_html
    assert 'id="perf-db-count"' in index_html
    assert 'id="perf-db-telemetry"' in index_html
    assert 'id="perf-db-sample-id"' in index_html
    assert 'id="perf-result-box"' in index_html
    assert "runPerformanceWorkloadBenchmark" in index_html
    assert "fetchLivePerformanceStats" in index_html

    # 2. Verify clinician UI elements in apps/clinician-web/copilot_dashboard.html
    clinician_html_path = Path("apps/clinician-web/copilot_dashboard.html")
    assert clinician_html_path.exists(), "copilot_dashboard.html not found!"
    clinician_html = clinician_html_path.read_text(encoding="utf-8")

    assert 'id="clinicianPerfSlaWidget"' in clinician_html
    assert 'id="clinician-p95-badge"' in clinician_html
    assert 'id="btn-clinician-benchmark"' in clinician_html
    assert "runClinicianRiskBenchmark" in clinician_html
    assert "initClinicianSlaBadge" in clinician_html

    # 3. End-to-end Database verification via API
    resp = client.post("/api/v1/risk/benchmark/workload?workload_size=25")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "PASSED"
    assert data["workload_size"] == 25
    assert data["sla_met"] is True
    assert data["percentiles"]["p95_ms"] <= 2000.0

    db_info = data["database_verification"]
    assert db_info["verified"] is True
    assert db_info["persisted_records_count"] == 25
    assert db_info["table"] == "risk_assessments"
    sample_id = db_info["sample_persisted_id"]
    assert sample_id is not None

    # Audit lookup directly in database store
    stored = store.get_risk_assessment(sample_id)
    assert stored is not None, f"Sample record {sample_id} not found in database store!"
    assert stored.model_name == "NCD_COMPOSITE_RISK_ENGINE"
    assert stored.model_version == ncd_risk_engine.engine_version
    assert stored.input_snapshot is not None
    assert "AGE" in stored.input_snapshot
    assert stored.evaluated_at is not None

