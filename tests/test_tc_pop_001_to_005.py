"""Comprehensive Test Suite for SevaHealth Population Health Intelligence Test Cases:
TC-POP-001: Screening aggregate accuracy (P0)
TC-POP-002: Risk distribution accuracy (P0)
TC-POP-003: Organization isolation (P0)
TC-POP-004: Small-group privacy (P0)
TC-POP-005: Simulated data labelling (P1)
"""

import pytest
from starlette.testclient import TestClient

from packages.types.enums import UserRole
from packages.auth.jwt import TokenPayload, create_access_token, get_password_hash
from services.api.main import app
from services.store import store, UserRecord
from services.population_intelligence.analytics import (
    seed_synthetic_cohort,
    remove_synthetic_cohort,
    get_population_overview,
    get_risk_distribution,
    get_demographic_breakdown,
)
from services.population_intelligence.models import (
    SeedCohortRequest,
    SeedCohortCitizen,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def cleanup_test_cohorts():
    """Ensures test cohorts are cleaned up before and after tests."""
    yield
    remove_synthetic_cohort("prog-tc-pop-001")
    remove_synthetic_cohort("prog-tc-pop-002")
    remove_synthetic_cohort("prog-tc-pop-003-a")
    remove_synthetic_cohort("prog-tc-pop-003-b")
    remove_synthetic_cohort("prog-tc-pop-004")
    remove_synthetic_cohort("prog-tc-pop-005")


@pytest.fixture
def sysadmin_token():
    return create_access_token(
        subject="sysadmin-user-01",
        tenant_id="karnataka_state_health",
        role=UserRole.SYSTEM_ADMIN,
    )


@pytest.fixture
def pha_global_token():
    return create_access_token(
        subject="admin-global-pha",
        tenant_id="karnataka_state_health",
        role=UserRole.PUBLIC_HEALTH_ADMIN,
    )


# ==============================================================================
# TC-POP-001: Screening Aggregate Accuracy (Priority: P0)
# ==============================================================================

class TestTCPOP001ScreeningAggregateAccuracy:
    """TC-POP-001: Screening aggregate accuracy.
    Steps:
    1. Seed a known set of synthetic citizens and screenings.
    2. Calculate the expected count independently.
    3. Retrieve the dashboard total.
    Expected result: The displayed count matches the defined inclusion rules and underlying test data.
    """

    def test_screening_aggregate_matches_inclusion_rules(self, pha_global_token):
        program_id = "prog-tc-pop-001"
        citizens = []

        # 1. 10 Citizens with COMPLETED screenings
        for i in range(1, 11):
            citizens.append(
                SeedCohortCitizen(
                    id=f"cit-pop-001-{i:02d}",
                    name=f"Citizen Completed {i}",
                    age=30 + i,
                    gender="MALE" if i % 2 == 0 else "FEMALE",
                    district="Mysuru",
                    program_id=program_id,
                    screening_status="COMPLETED",
                    risk_tier="LOW",
                )
            )

        # 2. 3 Citizens with DRAFT screenings (should be EXCLUDED by inclusion rules)
        for i in range(11, 14):
            citizens.append(
                SeedCohortCitizen(
                    id=f"cit-pop-001-{i:02d}",
                    name=f"Citizen Draft {i}",
                    age=25 + i,
                    gender="FEMALE",
                    district="Mysuru",
                    program_id=program_id,
                    screening_status="DRAFT",
                    risk_tier="LOW",
                )
            )

        # 3. 2 Citizens with NO screenings (should be EXCLUDED from screened total)
        for i in range(14, 16):
            citizens.append(
                SeedCohortCitizen(
                    id=f"cit-pop-001-{i:02d}",
                    name=f"Citizen Unscreened {i}",
                    age=40 + i,
                    gender="MALE",
                    district="Mysuru",
                    program_id=program_id,
                    screening_status=None,
                    risk_tier=None,
                )
            )

        # Seed cohort
        seed_req = SeedCohortRequest(
            program_id=program_id,
            program_name="TC-POP-001 Mysuru NCD Screening Initiative",
            district="Mysuru",
            citizens=citizens,
        )
        seed_synthetic_cohort(seed_req)

        # Independent calculation based on defined inclusion rules
        expected_target_population = 15
        expected_completed_screened = 10
        expected_completion_rate = round((10 / 15) * 100.0, 1)  # 66.7%

        # Retrieve dashboard overview for the program
        resp = client.get(
            f"/api/v1/population/overview?program={program_id}",
            headers={"Authorization": f"Bearer {pha_global_token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Assert dashboard totals strictly match independent calculation
        assert data["total_target_population"] == expected_target_population
        assert data["population_screened"] == expected_completed_screened
        assert data["screening_completion_rate_pct"] == expected_completion_rate
        assert data["selected_program"] == program_id


# ==============================================================================
# TC-POP-002: Risk Distribution Accuracy (Priority: P0)
# ==============================================================================

class TestTCPOP002RiskDistributionAccuracy:
    """TC-POP-002: Risk distribution accuracy.
    Steps:
    1. Seed known risk categories.
    2. Independently calculate category totals.
    3. Compare them with the dashboard.
    Expected result: Counts and denominators are correct, including treatment of insufficient-data cases.
    """

    def test_risk_distribution_and_insufficient_data_accuracy(self, pha_global_token):
        program_id = "prog-tc-pop-002"
        citizens = []

        # Known risk categories:
        # 5 LOW, 6 MODERATE, 4 HIGH, 2 CRITICAL, 3 INSUFFICIENT_DATA (Total = 20)
        risk_plan = [
            ("LOW", 5),
            ("MODERATE", 6),
            ("HIGH", 4),
            ("CRITICAL", 2),
            ("INSUFFICIENT_DATA", 3),
        ]

        idx = 1
        for tier, count in risk_plan:
            for _ in range(count):
                citizens.append(
                    SeedCohortCitizen(
                        id=f"cit-pop-002-{idx:02d}",
                        name=f"Citizen Risk {idx}",
                        age=35 + (idx % 25),
                        gender="MALE" if idx % 2 == 0 else "FEMALE",
                        district="Mandya",
                        program_id=program_id,
                        screening_status="COMPLETED",
                        risk_tier=tier,
                        has_complete_data=(tier != "INSUFFICIENT_DATA"),
                    )
                )
                idx += 1

        seed_req = SeedCohortRequest(
            program_id=program_id,
            program_name="TC-POP-002 Mandya Glycemic & Vascular Audit",
            district="Mandya",
            citizens=citizens,
        )
        seed_synthetic_cohort(seed_req)

        # Independently calculate expected totals
        expected_low = 5
        expected_moderate = 6
        expected_high = 4
        expected_critical = 2
        expected_insufficient = 3
        expected_denominator = 20

        # Query dashboard risk distribution
        resp = client.get(
            f"/api/v1/population/risk-distribution?program={program_id}",
            headers={"Authorization": f"Bearer {pha_global_token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        tiers = data["overall_tiers"]
        assert tiers["LOW"] == expected_low
        assert tiers["MODERATE"] == expected_moderate
        assert tiers["HIGH"] == expected_high
        assert tiers["CRITICAL"] == expected_critical
        assert tiers["INSUFFICIENT_DATA"] == expected_insufficient

        # Denominator verification: sum of tiers must strictly equal total_screened
        assert data["total_screened"] == expected_denominator
        assert sum(tiers.values()) == expected_denominator
        assert data["insufficient_data_count"] == expected_insufficient

        # Verify insufficient data cases are never misclassified as LOW or omitted
        assert data["demographic_cross_tabulation"]["insufficient_data_pct"] == round((3 / 20) * 100.0, 1)


# ==============================================================================
# TC-POP-003: Organization Isolation (Priority: P0)
# ==============================================================================

class TestTCPOP003OrganizationIsolation:
    """TC-POP-003: Organization isolation.
    Steps:
    1. Seed data for two independent programs.
    2. Authenticate as an administrator of Program A.
    3. Query permitted aggregates.
    4. Attempt to retrieve Program B data.
    Expected result: The dashboard respects program authorization boundaries.
    """

    def test_program_authorization_isolation_boundary(self, sysadmin_token):
        prog_a = "prog-tc-pop-003-a"
        prog_b = "prog-tc-pop-003-b"

        # 1. Seed Program A data (10 citizens)
        citizens_a = [
            SeedCohortCitizen(
                id=f"cit-pop-003a-{i:02d}",
                name=f"Program A Citizen {i}",
                age=40,
                gender="MALE",
                district="Mysuru",
                program_id=prog_a,
                screening_status="COMPLETED",
                risk_tier="LOW",
            )
            for i in range(1, 11)
        ]
        seed_synthetic_cohort(SeedCohortRequest(
            program_id=prog_a,
            program_name="Program A: Mysuru Hypertension Control",
            district="Mysuru",
            citizens=citizens_a,
        ))

        # 2. Seed Program B data (8 citizens)
        citizens_b = [
            SeedCohortCitizen(
                id=f"cit-pop-003b-{i:02d}",
                name=f"Program B Citizen {i}",
                age=50,
                gender="FEMALE",
                district="Mandya",
                program_id=prog_b,
                screening_status="COMPLETED",
                risk_tier="HIGH",
            )
            for i in range(1, 9)
        ]
        seed_synthetic_cohort(SeedCohortRequest(
            program_id=prog_b,
            program_name="Program B: Mandya Diabetes Outreach",
            district="Mandya",
            citizens=citizens_b,
        ))

        # Register Admin A with assignment strictly to Program A
        admin_a = UserRecord(
            id="admin-pop-prog-a",
            tenant_id="karnataka_state_health",
            email="admin_a@mysuru-health.gov.in",
            hashed_password=get_password_hash("password123"),
            role=UserRole.PUBLIC_HEALTH_ADMIN,
            full_name="Dr. Program A Director",
            assigned_programs=[prog_a],
        )
        store.add_user(admin_a)

        token_a = create_access_token(
            subject=admin_a.id,
            tenant_id="karnataka_state_health",
            role=UserRole.PUBLIC_HEALTH_ADMIN,
            scopes=[f"program:{prog_a}"],
        )

        # 3. Query permitted aggregates for Program A -> Expect HTTP 200 OK
        resp_a = client.get(
            f"/api/v1/population/overview?program={prog_a}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_a.status_code == 200, resp_a.text
        assert resp_a.json()["population_screened"] == 10
        assert resp_a.json()["selected_program"] == prog_a

        # 4. Attempt to retrieve Program B data as Admin A -> Expect HTTP 403 Forbidden!
        resp_b_denied = client.get(
            f"/api/v1/population/overview?program={prog_b}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_b_denied.status_code == 403
        assert "not authorized to access program" in resp_b_denied.json()["detail"]

        # Attempt to retrieve Program B risk distribution as Admin A -> Expect HTTP 403 Forbidden!
        resp_risk_denied = client.get(
            f"/api/v1/population/risk-distribution?program={prog_b}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_risk_denied.status_code == 403

        # System Admin with global clearance can access both Program A and Program B
        resp_sys_a = client.get(
            f"/api/v1/population/overview?program={prog_a}",
            headers={"Authorization": f"Bearer {sysadmin_token}"},
        )
        assert resp_sys_a.status_code == 200

        resp_sys_b = client.get(
            f"/api/v1/population/overview?program={prog_b}",
            headers={"Authorization": f"Bearer {sysadmin_token}"},
        )
        assert resp_sys_b.status_code == 200


# ==============================================================================
# TC-POP-004: Small-Group Privacy (Priority: P0)
# ==============================================================================

class TestTCPOP004SmallGroupPrivacy:
    """TC-POP-004: Small-group privacy.
    Steps:
    1. Seed a group smaller than the configured minimum aggregation threshold (k < 10).
    2. Request demographic breakdowns.
    3. Attempt multiple filters that could expose the same group.
    Expected result: The dashboard applies approved suppression or equivalent privacy controls
    and does not reveal protected individuals through repeated filtering.
    """

    def test_small_group_privacy_suppression(self, pha_global_token):
        program_id = "prog-tc-pop-004"
        citizens = []

        # Seed 12 Males, 15 Females, and only 4 "OTHER" gender (4 < 10 threshold!)
        for i in range(1, 13):
            citizens.append(SeedCohortCitizen(
                id=f"cit-pop-004-m-{i}", name=f"Male {i}", age=35, gender="MALE", district="Mysuru", program_id=program_id
            ))
        for i in range(1, 16):
            citizens.append(SeedCohortCitizen(
                id=f"cit-pop-004-f-{i}", name=f"Female {i}", age=45, gender="FEMALE", district="Mysuru", program_id=program_id
            ))
        for i in range(1, 5):
            citizens.append(SeedCohortCitizen(
                id=f"cit-pop-004-o-{i}", name=f"Other {i}", age=28, gender="OTHER", district="Mysuru", program_id=program_id
            ))

        seed_req = SeedCohortRequest(
            program_id=program_id,
            program_name="TC-POP-004 Small Group Anonymity Cohort",
            district="Mysuru",
            citizens=citizens,
        )
        seed_synthetic_cohort(seed_req)

        # 2. Request demographic breakdown
        resp = client.get(
            f"/api/v1/population/demographic-breakdown?program={program_id}&dimension=sex",
            headers={"Authorization": f"Bearer {pha_global_token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        breakdown = data["breakdown"]
        # MALE (12 >= 10) -> Unsuppressed
        assert breakdown["MALE"]["is_suppressed"] is False
        assert breakdown["MALE"]["value"] == 12

        # FEMALE (15 >= 10) -> Unsuppressed
        assert breakdown["FEMALE"]["is_suppressed"] is False
        assert breakdown["FEMALE"]["value"] == 15

        # OTHER (4 < 10) -> MUST BE SUPPRESSED!
        assert breakdown["OTHER"]["is_suppressed"] is True
        assert breakdown["OTHER"]["value"] is None
        assert breakdown["OTHER"]["display"] == "< 10 (Suppressed for Privacy)"

        # 3. Attempt repeated queries and overview queries
        overview_resp = client.get(
            f"/api/v1/population/overview?program={program_id}",
            headers={"Authorization": f"Bearer {pha_global_token}"},
        )
        assert overview_resp.status_code == 200
        ov_data = overview_resp.json()
        assert ov_data["demographic_summary"]["sex"]["OTHER"] == "< 10 (Suppressed for Privacy)"


# ==============================================================================
# TC-POP-005: Simulated Data Labelling (Priority: P1)
# ==============================================================================

class TestTCPOP005SimulatedDataLabelling:
    """TC-POP-005: Simulated data labelling.
    Steps:
    1. Load synthetic demo records.
    2. Inspect dashboard labels, charts and exported summaries.
    Expected result: Simulated data is identified as demo data and is not represented as real community outcomes.
    """

    def test_simulated_data_provenance_labels_and_disclaimers(self, pha_global_token):
        # 1. Overview endpoint inspection
        resp_ov = client.get(
            "/api/v1/population/overview?district=Mysuru",
            headers={"Authorization": f"Bearer {pha_global_token}"},
        )
        assert resp_ov.status_code == 200
        data_ov = resp_ov.json()
        assert data_ov["is_simulated"] is True
        assert data_ov["data_provenance"] == "SYNTHETIC_DEMONSTRATION_DATA"
        assert "DEMONSTRATION ONLY" in data_ov["provenance_disclaimer"]

        # 2. Risk distribution endpoint inspection
        resp_rd = client.get(
            "/api/v1/population/risk-distribution?district=Mysuru",
            headers={"Authorization": f"Bearer {pha_global_token}"},
        )
        assert resp_rd.status_code == 200
        data_rd = resp_rd.json()
        assert data_rd["is_simulated"] is True
        assert data_rd["data_provenance"] == "SYNTHETIC_DEMONSTRATION_DATA"

        # 3. Trends endpoint inspection
        resp_tr = client.get(
            "/api/v1/population/risk-trends?district=Mysuru",
            headers={"Authorization": f"Bearer {pha_global_token}"},
        )
        assert resp_tr.status_code == 200
        data_tr = resp_tr.json()
        assert data_tr["is_simulated"] is True
        assert data_tr["data_provenance"] == "SYNTHETIC_DEMONSTRATION_DATA"

        # 4. High-risk cohorts inspection
        resp_ch = client.get(
            "/api/v1/population/high-risk-cohorts?district=Mysuru",
            headers={"Authorization": f"Bearer {pha_global_token}"},
        )
        assert resp_ch.status_code == 200
        data_ch = resp_ch.json()
        assert data_ch["is_simulated"] is True
        assert data_ch["data_provenance"] == "SYNTHETIC_DEMONSTRATION_DATA"

        # 5. Export summary inspection
        resp_exp = client.get(
            "/api/v1/population/deidentified-export?limit=10",
            headers={"Authorization": f"Bearer {pha_global_token}"},
        )
        assert resp_exp.status_code == 200
        data_exp = resp_exp.json()
        assert data_exp["is_simulated"] is True
        assert data_exp["data_provenance"] == "SYNTHETIC_DEMONSTRATION_DATA"
        assert len(data_exp["records"]) == 10
        for rec in data_exp["records"]:
            assert rec["data_provenance"] == "SYNTHETIC_DEMONSTRATION_DATA"
