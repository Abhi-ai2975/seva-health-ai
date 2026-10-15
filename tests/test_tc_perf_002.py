"""Section 16: Test Cases — Performance and Reliability.

TC-PERF-002: Dashboard response time (Priority: P1)

Steps:
1. Load the approved synthetic population dataset.
2. Execute representative dashboard queries.
3. Measure latency.
Expected result:
The agreed dashboard p95 latency target is met (initially agreed as no more than 2.0 seconds).
Verify database queries, metrics tracking, and data integrity.
"""

import time
import math
import statistics
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from services.api.main import app
from services.store import store
from packages.observability.metrics import metrics
from services.population_intelligence.analytics import (
    get_population_overview,
    get_risk_distribution,
    get_risk_trends,
    get_high_risk_cohorts,
    get_intervention_outcomes,
    get_referral_pipeline,
    get_community_comparison,
    get_demographic_breakdown,
    get_deidentified_export,
    DISTRICT_BASELINES,
)
from services.population_intelligence.router import ensure_approved_synthetic_population_dataset

client = TestClient(app)


# ==============================================================================
# TC-PERF-002: TEST SUITE
# ==============================================================================

def test_tc_perf_002_dashboard_response_time_latency_percentiles():
    """TC-PERF-002: Loads approved synthetic dataset, executes representative dashboard queries,
    measures latency percentiles, and verifies p95 <= 2.0s SLA target.
    """
    # Step 1: Load the approved synthetic population dataset
    dataset_meta = ensure_approved_synthetic_population_dataset()
    assert dataset_meta["total_citizens"] >= 25, "Approved synthetic cohort not loaded"
    assert len(dataset_meta["districts_covered"]) >= 5, "Karnataka districts missing"

    # Step 2: Define representative dashboard queries
    queries = [
        ("Overview (State)", lambda: get_population_overview(district=None, program=None)),
        ("Overview (Mysuru)", lambda: get_population_overview(district="Mysuru", program=None)),
        ("Risk Distribution", lambda: get_risk_distribution(district="All", program="All")),
        ("Longitudinal Trends", lambda: get_risk_trends()),
        ("High Risk Cohorts", lambda: get_high_risk_cohorts()),
        ("Intervention Outcomes", lambda: get_intervention_outcomes()),
        ("Referral Pipeline", lambda: get_referral_pipeline()),
        ("Community Comparison", lambda: get_community_comparison()),
        ("Demographic Breakdown", lambda: get_demographic_breakdown(district="All", program="All", dimension="age_group")),
        ("Deidentified Export", lambda: get_deidentified_export(limit=25)),
    ]

    iterations = 5
    latencies_ms = []

    # Step 3: Execute representative dashboard queries and measure latency
    for _ in range(iterations):
        for q_name, q_fn in queries:
            t0 = time.perf_counter()
            res = q_fn()
            duration_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(duration_ms)

            # Record metrics & database query telemetry
            metrics.record_dashboard_query(q_name, duration_ms)
            metrics.record_db_query("SELECT", "population_screenings", duration_ms, success=True)
            assert res is not None

    # Step 4: Calculate percentiles
    sorted_lats = sorted(latencies_ms)
    n = len(sorted_lats)
    assert n == iterations * len(queries)

    def calc_percentile(p: float) -> float:
        idx = min(int(math.ceil(p * n)) - 1, n - 1)
        return round(sorted_lats[max(0, idx)], 2)

    min_lat = round(sorted_lats[0], 2)
    max_lat = round(sorted_lats[-1], 2)
    avg_lat = round(statistics.mean(sorted_lats), 2)
    p50 = calc_percentile(0.50)
    p90 = calc_percentile(0.90)
    p95 = calc_percentile(0.95)
    p99 = calc_percentile(0.99)

    approved_target_ms = 2000.0  # 2.0s agreed target

    print(f"\n[TC-PERF-002 Dashboard Latency Benchmark] Total Queries={n}")
    print(f"  Min: {min_lat:.2f}ms | Avg: {avg_lat:.2f}ms")
    print(f"  p50: {p50:.2f}ms | p90: {p90:.2f}ms | p95: {p95:.2f}ms | p99: {p99:.2f}ms | Max: {max_lat:.2f}ms")
    print(f"  Approved Target: p95 <= {approved_target_ms:.0f}ms (2.0s)")

    # Assert p95 latency meets approved target
    assert p95 <= approved_target_ms, f"p95 latency ({p95:.2f}ms) exceeded 2.0s SLA target!"
    assert avg_lat < 100.0, f"Average latency ({avg_lat:.2f}ms) unexpectedly high!"

    # Database Verification: verify population records exist in store
    assert len(store.citizens) >= 25
    assert sum(len(s) for s in store.screenings.values()) >= 25


def test_tc_perf_002_api_dashboard_benchmark_endpoint():
    """TC-PERF-002: Validates the POST /api/v1/population/benchmark/dashboard endpoint."""
    resp = client.post("/api/v1/population/benchmark/dashboard?iterations=5")
    assert resp.status_code == 200, f"Benchmark endpoint failed: {resp.text}"
    data = resp.json()

    assert data["test_case"] == "TC-PERF-002"
    assert data["iterations"] == 5
    assert data["total_queries_executed"] == 50
    assert data["query_types_count"] == 10

    # Percentiles
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

    # Per-query breakdown
    breakdown = data["query_breakdown"]
    assert "Overview (State)" in breakdown
    assert "Risk Distribution" in breakdown
    assert "Deidentified Export" in breakdown
    assert breakdown["Overview (State)"]["meets_sla"] is True

    # Database verification
    db_verify = data["database_verification"]
    assert db_verify["verified"] is True
    assert db_verify["table"] == "population_screenings"
    assert db_verify["db_query_recorded"] is True
    assert db_verify["citizens_in_store"] >= 25


def test_tc_perf_002_live_dashboard_percentiles_telemetry_endpoint():
    """TC-PERF-002: Validates the GET /api/v1/population/performance/percentiles telemetry endpoint."""
    resp = client.get("/api/v1/population/performance/percentiles")
    assert resp.status_code == 200
    data = resp.json()

    assert data["test_case"] == "TC-PERF-002"
    assert data["metric"] == "dashboard_response_time"
    assert data["approved_target_seconds"] == 2.0
    assert data["sla_status"] == "COMPLIANT"
    assert data["p95_ms"] <= 2000.0


def test_tc_perf_002_concurrent_dashboard_queries_under_sla():
    """TC-PERF-002: Simulates concurrent multi-threaded dashboard queries and verifies p95 latency <= 2.0s."""
    queries = [
        lambda: get_population_overview(district=None, program=None),
        lambda: get_risk_distribution(district="All", program="All"),
        lambda: get_risk_trends(),
        lambda: get_community_comparison(),
        lambda: get_demographic_breakdown(district="All", program="All", dimension="age_group"),
    ]

    latencies = []

    def run_query_task(idx: int) -> float:
        fn = queries[idx % len(queries)]
        t0 = time.perf_counter()
        res = fn()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert res is not None
        return elapsed_ms

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(run_query_task, range(30)))
        latencies.extend(results)

    sorted_lats = sorted(latencies)
    p95 = sorted_lats[int(len(sorted_lats) * 0.95)]

    print(f"\n[Concurrent Dashboard Latency Benchmark] Total: {len(latencies)}, p95: {p95:.2f}ms")
    assert p95 <= 2000.0, f"Concurrent dashboard p95 latency exceeded 2.0s SLA target: {p95:.2f}ms"


def test_tc_perf_002_frontend_ui_and_database_verification():
    """TC-PERF-002: Verifies dashboard SLA frontend elements in public-health-web and index.html,
    and performs database verification of population records and query telemetry.
    """
    # 1. Verify static UI elements in apps/public-health-web/index.html
    public_health_html_path = Path("apps/public-health-web/index.html")
    assert public_health_html_path.exists(), "public-health-web/index.html not found!"
    pub_html = public_health_html_path.read_text(encoding="utf-8")

    assert 'id="dashPerfSlaWidget"' in pub_html or "openDashboardPerfModal" in pub_html
    assert "runDashboardWorkloadBenchmark" in pub_html or "runDashboardBenchmark" in pub_html

    # 2. Verify static UI elements in services/api/static/index.html
    index_html_path = Path("services/api/static/index.html")
    assert index_html_path.exists(), "index.html not found!"
    index_html = index_html_path.read_text(encoding="utf-8")

    assert "openDashboardPerformanceModal" in index_html
    assert 'id="dashboard-performance-sla-modal"' in index_html
    assert 'id="dash-kpi-p95"' in index_html

    # 3. End-to-end Database & Query audit
    resp = client.post("/api/v1/population/benchmark/dashboard?iterations=3")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "PASSED"
    assert data["sla_met"] is True
    assert data["percentiles"]["p95_ms"] <= 2000.0

    db_info = data["database_verification"]
    assert db_info["verified"] is True
    assert db_info["citizens_in_store"] >= 25
    assert db_info["screenings_in_store"] >= 25
