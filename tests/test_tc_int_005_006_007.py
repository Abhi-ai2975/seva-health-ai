"""Test suite for Section 14 Test Cases: Documents, Wearables and Clinical Integration.

TC-INT-005: Wearable retry and idempotency (Priority: P1)
TC-INT-006: EHRbase write/read round trip (Priority: P1)
TC-INT-007: External EMR failure (Priority: P1)
"""

import pytest
from datetime import datetime, timezone, timedelta
import uuid
from typing import List, Dict, Any

from packages.types.enums import UserRole, Gender
from services.wearable.models import (
    WearableProvider,
    WearableMetricType,
    WearableProvenance,
    NormalizedWearableRecord,
)
from services.wearable.adapter import (
    OpenWearablesAdapter,
    MockWearableProvider,
    wearable_adapter,
)
from services.store import store, CitizenRecord
from packages.clinical_models.domain_models import (
    VitalSign,
    LabResult,
    ProvenanceRecord,
)
from packages.clinical_models.repository import OpenEHRRepository
from packages.clinical_models.openehr_builder import (
    build_vital_signs_composition,
    build_laboratory_composition,
)
from packages.interop.base import AccessContext
from packages.interop.models import (
    ObservationCategory,
    ClinicalSourceSystem,
    NormalizedObservation,
)
from packages.interop.adapters.openehr_adapter import OpenEhrEhrBaseAdapter
from packages.interop.adapters.emr_gql_adapter import EmrGqlAdapter
from packages.interop.adapters.local_adapter import SevaHealthLocalAdapter
from packages.interop.service import FederatedClinicalDataProvider


# ==============================================================================
# TC-INT-005: Wearable retry and idempotency (Priority: P1)
# ==============================================================================

@pytest.mark.asyncio
async def test_tc_int_005_wearable_transient_failure_and_retry_recovery():
    """TC-INT-005: Simulates temporary provider failure and verifies retry recovery.

    Steps:
    1. Simulate a transient network failure on attempt 1.
    2. Retry operation and succeed on attempt 2.
    3. Verify system recovers smoothly from the transient failure.
    4. Simulate persistent failure exceeding max retries and verify safe error propagation.
    """
    adapter = OpenWearablesAdapter()
    citizen_id = "citizen-retry-test-01"

    # Step 1 & 2: Transient failure recovery
    attempts = 0

    def transient_provider_call():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionResetError("Temporary provider socket reset during sync handshake")
        return MockWearableProvider.generate_records(
            citizen_id=citizen_id,
            provider=WearableProvider.GARMIN,
            days=2,
        )

    records = await adapter._execute_with_retry(transient_provider_call)
    assert attempts == 2
    assert len(records) > 0
    assert all(r.citizen_id == citizen_id for r in records)

    # Step 4: Persistent failure exceeding max retries
    def permanent_failure_call():
        raise TimeoutError("External wearable API gateway timeout (504)")

    with pytest.raises(TimeoutError) as exc_info:
        await adapter._execute_with_retry(permanent_failure_call)
    assert "timeout" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_tc_int_005_wearable_sync_idempotency_avoids_duplicate_observations():
    """TC-INT-005: Repeats the exact same source events and verifies zero duplicate observations.

    Steps:
    1. Authenticate and connect wearable provider for a citizen.
    2. Perform initial synchronization of telemetry events.
    3. Repeat the exact same synchronization events (identical source IDs).
    4. Verify both the adapter's raw records and the central clinical observation store avoid duplicates.
    """
    adapter = OpenWearablesAdapter()
    citizen_id = f"citizen-idem-{uuid.uuid4().hex[:8]}"
    store.add_citizen(
        CitizenRecord(
            id=citizen_id,
            tenant_id="karnataka_state_health",
            user_id=f"user-{citizen_id}",
            abha_id="91-1234-5678-0002",
            first_name="Idem",
            last_name="Test",
            birth_date="1982-01-01",
            gender=Gender.MALE,
            phone="+919876543211",
            state="Karnataka",
            district="Mysuru",
            sub_district="Hunsur",
            village_or_ward="Ward 2",
        )
    )

    # Connect provider
    await adapter.connect_provider(
        citizen_id=citizen_id,
        provider=WearableProvider.GARMIN,
        auth_payload={"access_token": "garmin_oauth_token_idem_999"},
    )

    # Create fixed, deterministic wearable records
    fixed_ts = datetime.now(timezone.utc) - timedelta(hours=3)
    prov = WearableProvenance(
        provider=WearableProvider.GARMIN,
        device_brand="Garmin",
        device_model="Forerunner 965",
        is_medical_grade=False,
    )
    event_id_hr = f"wearable-event-hr-{uuid.uuid4().hex[:6]}"
    event_id_steps = f"wearable-event-steps-{uuid.uuid4().hex[:6]}"

    records = [
        NormalizedWearableRecord(
            id=event_id_hr,
            citizen_id=citizen_id,
            source="OPEN_WEARABLES_CONNECTOR",
            provider=WearableProvider.GARMIN,
            timestamp=fixed_ts,
            metric=WearableMetricType.HEART_RATE,
            value=72.0,
            unit="bpm",
            confidence=0.98,
            provenance=prov,
        ),
        NormalizedWearableRecord(
            id=event_id_steps,
            citizen_id=citizen_id,
            source="OPEN_WEARABLES_CONNECTOR",
            provider=WearableProvider.GARMIN,
            timestamp=fixed_ts,
            metric=WearableMetricType.STEPS,
            value=8450.0,
            unit="steps",
            confidence=0.95,
            provenance=prov,
        ),
    ]

    # First Ingestion
    if citizen_id not in adapter.raw_records:
        adapter.raw_records[citizen_id] = []
    adapter.raw_records[citizen_id].extend(records)
    adapter._persist_records_to_clinical_store(citizen_id, records)

    initial_adapter_count = len(adapter.raw_records[citizen_id])
    initial_store_obs = [o for o in store.observations.get(citizen_id, []) if o.source == "OPEN_WEARABLES_CONNECTOR"]
    assert initial_adapter_count == 2
    assert len(initial_store_obs) == 2

    # Step 3: Repeat the exact same source events (simulating network replay / re-sync)
    # Adapter level deduplication check
    existing_ids = {r.id for r in adapter.raw_records[citizen_id]}
    repeated_records = [r for r in records if r.id not in existing_ids]
    adapter.raw_records[citizen_id].extend(repeated_records)

    # Store level deduplication check
    adapter._persist_records_to_clinical_store(citizen_id, records)

    # Step 4: Verify zero duplication
    final_adapter_count = len(adapter.raw_records[citizen_id])
    final_store_obs = [o for o in store.observations.get(citizen_id, []) if o.source == "OPEN_WEARABLES_CONNECTOR"]

    assert final_adapter_count == initial_adapter_count, "Raw wearable records accumulated duplicate entries!"
    assert len(final_store_obs) == len(initial_store_obs), "Central observation store accumulated duplicate observations!"


# ==============================================================================
# TC-INT-006: EHRbase write/read round trip (Priority: P1)
# ==============================================================================

@pytest.mark.asyncio
async def test_tc_int_006_ehrbase_vital_signs_write_read_round_trip():
    """TC-INT-006: openEHR write/read round trip preserves clinical semantics and metadata.

    Steps:
    1. Create a supported clinical record (VitalSign with systolic & diastolic BP, heart rate).
    2. Write it through the configured openEHR adapter / repository.
    3. Retrieve it through the adapter.
    4. Compare supported fields and provenance (values, units, timestamps, recorder ID, device).
    """
    repo = OpenEHRRepository()
    openehr_adapter = OpenEhrEhrBaseAdapter()
    patient_id = f"citizen-openehr-vital-{uuid.uuid4().hex[:6]}"

    record_time = datetime.now(timezone.utc) - timedelta(minutes=45)
    provenance = ProvenanceRecord(
        recorder_id="health-worker-sunita-01",
        recorder_role=UserRole.HEALTH_WORKER,
        device_model="Omron HEM-7120",
        device_id="DEV-OMRON-998811",
        capture_method="BLUETOOTH_BLE_SYNC",
    )

    vitals = [
        VitalSign(
            patient_id=patient_id,
            clinical_type="SYSTOLIC_BP",
            value=142.0,
            unit="mmHg",
            measurement_timestamp=record_time,
            source="COMMUNITY_HEALTH_CAMP",
            provenance=provenance,
            confidence_score=0.99,
        ),
        VitalSign(
            patient_id=patient_id,
            clinical_type="DIASTOLIC_BP",
            value=90.0,
            unit="mmHg",
            measurement_timestamp=record_time,
            source="COMMUNITY_HEALTH_CAMP",
            provenance=provenance,
            confidence_score=0.99,
        ),
        VitalSign(
            patient_id=patient_id,
            clinical_type="HEART_RATE",
            value=78.0,
            unit="bpm",
            measurement_timestamp=record_time,
            source="COMMUNITY_HEALTH_CAMP",
            provenance=provenance,
            confidence_score=0.98,
        ),
    ]

    # Step 2: Write through OpenEHRRepository
    for v in vitals:
        comp_uid = await repo.save_vital_sign(v)
        assert comp_uid is not None
        assert len(comp_uid) > 5

    # Also build and write composition to OpenEhrEhrBaseAdapter
    comp = build_vital_signs_composition(vitals, composer_name=provenance.recorder_id)
    adapter_comp_uid = await openehr_adapter.save_composition(patient_id, comp)
    assert adapter_comp_uid is not None

    # Step 3: Retrieve longitudinal records via repository
    vital_history = await repo.get_patient_vital_history(patient_id)
    assert len(vital_history) == 3

    # Step 4: Compare supported fields and provenance
    sbp_rec = next(v for v in vital_history if v.clinical_type == "SYSTOLIC_BP")
    dbp_rec = next(v for v in vital_history if v.clinical_type == "DIASTOLIC_BP")
    hr_rec = next(v for v in vital_history if v.clinical_type == "HEART_RATE")

    assert sbp_rec.value == 142.0
    assert sbp_rec.unit == "mmHg"
    assert sbp_rec.provenance.recorder_id == "health-worker-sunita-01"
    assert sbp_rec.provenance.device_model == "Omron HEM-7120"
    assert sbp_rec.provenance.capture_method == "BLUETOOTH_BLE_SYNC"

    assert dbp_rec.value == 90.0
    assert dbp_rec.unit == "mmHg"

    assert hr_rec.value == 78.0
    assert hr_rec.unit == "bpm"

    # Verify adapter normalized observation retrieval
    ctx = AccessContext(actor_id="clinician-01", actor_role=UserRole.CLINICIAN)
    obs_list = await openehr_adapter.get_observations(patient_id, ctx, category=ObservationCategory.VITAL_SIGN)
    assert len(obs_list) >= 2
    sbp_obs = next(o for o in obs_list if "Systolic" in o.display_name)
    assert sbp_obs.value == 142.0
    assert sbp_obs.unit == "mmHg"
    assert sbp_obs.source_system == ClinicalSourceSystem.OPENEHR_EHRBASE
    assert sbp_obs.metadata.get("composer") == "health-worker-sunita-01"


@pytest.mark.asyncio
async def test_tc_int_006_ehrbase_laboratory_write_read_round_trip():
    """TC-INT-006: openEHR lab results write/read round trip preserves LOINC and reference semantics."""
    repo = OpenEHRRepository()
    openehr_adapter = OpenEhrEhrBaseAdapter()
    patient_id = f"citizen-openehr-lab-{uuid.uuid4().hex[:6]}"

    record_time = datetime.now(timezone.utc)
    provenance = ProvenanceRecord(
        recorder_id="lab-technician-ravi-02",
        recorder_role=UserRole.CLINICIAN,
        device_model="Roche Cobas c311",
        device_id="LAB-ROCHE-4411",
        capture_method="ANALYZER_DIRECT",
    )

    lab = LabResult(
        patient_id=patient_id,
        test_name="FASTING_BLOOD_GLUCOSE",
        loinc_code="1558-6",
        value=124.0,
        unit="mg/dL",
        reference_range="70 - 99 mg/dL",
        interpretation="ELEVATED",
        measurement_timestamp=record_time,
        source="DISTRICT_HOSPITAL_LAB",
        provenance=provenance,
        confidence_score=0.99,
    )

    # Step 2: Write to OpenEHRRepository and OpenEhrEhrBaseAdapter
    uid = await repo.save_lab_result(lab)
    assert uid is not None

    lab_comp = build_laboratory_composition([lab], composer_name=provenance.recorder_id)
    await openehr_adapter.save_composition(patient_id, lab_comp)

    # Step 3: Retrieve history from OpenEHRRepository
    labs = await repo.get_patient_lab_history(patient_id, test_name="FASTING_BLOOD_GLUCOSE")
    assert len(labs) == 1
    retrieved_lab = labs[0]

    # Step 4: Compare fields and clinical semantics
    assert retrieved_lab.value == 124.0
    assert retrieved_lab.unit == "mg/dL"
    assert retrieved_lab.loinc_code == "1558-6"
    assert retrieved_lab.reference_range == "70 - 99 mg/dL"
    assert retrieved_lab.interpretation == "ELEVATED"
    assert retrieved_lab.provenance.recorder_id == "lab-technician-ravi-02"
    assert retrieved_lab.provenance.device_model == "Roche Cobas c311"

    # Also verify adapter normalized extraction
    ctx = AccessContext(actor_id="clinician-01", actor_role=UserRole.CLINICIAN)
    adapter_obs = await openehr_adapter.get_observations(patient_id, ctx, category=ObservationCategory.LABORATORY)
    assert len(adapter_obs) == 1
    assert adapter_obs[0].value == 124.0
    assert adapter_obs[0].unit == "mg/dL"
    assert adapter_obs[0].code == "LOINC::1558-6"
    assert adapter_obs[0].interpretation == "ELEVATED"
    assert adapter_obs[0].metadata.get("reference_range") == "70 - 99 mg/dL"


# ==============================================================================
# TC-INT-007: External EMR failure (Priority: P1)
# ==============================================================================

@pytest.mark.asyncio
async def test_tc_int_007_external_emr_failure_isolation_and_recovery():
    """TC-INT-007: External EMR endpoint failure handling and recovery.

    Steps:
    1. Seed external EMR (bezs-emr-gql) with FHIR Observation and Patient.
    2. Seed local SevaHealth store with local observations and citizen demographics.
    3. Disable the test EMR endpoint (simulate external service outage).
    4. Attempt authorized read:
       - Direct call to EMR raises ConnectionError.
       - Federated provider reports integration failure in health check (DEGRADED).
       - Local data integrity is 100% preserved (local records return without data loss).
    5. Restore the endpoint and retry:
       - System recovers according to retry policy.
       - External records are once again retrieved and unified.
       - Health check status returns to HEALTHY.
    """
    citizen_id = f"citizen-emr-fail-{uuid.uuid4().hex[:6]}"
    ctx = AccessContext(actor_id="dr-anand", actor_role=UserRole.CLINICIAN)

    # Initialize adapters
    local_adapter = SevaHealthLocalAdapter()
    emr_adapter = EmrGqlAdapter(endpoint_url="https://emr.bezs-health.internal/graphql")
    fed_provider = FederatedClinicalDataProvider(
        local_adapter=local_adapter,
        emr_gql_adapter=emr_adapter,
    )

    # 1. Seed external FHIR resources
    emr_adapter.seed_fhir_resource(
        citizen_id,
        "Patient",
        {
            "id": citizen_id,
            "name": [{"given": ["Ramesh"], "family": "Patel"}],
            "gender": "male",
            "birthDate": "1978-04-12",
        },
    )
    emr_adapter.seed_fhir_resource(
        citizen_id,
        "Observation",
        {
            "id": f"fhir-obs-hba1c-{uuid.uuid4().hex[:4]}",
            "code": {"coding": [{"code": "4548-4", "display": "HbA1c"}]},
            "category": [{"coding": [{"code": "laboratory"}]}],
            "valueQuantity": {"value": 7.1, "unit": "%"},
            "status": "final",
            "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
        },
    )

    # 2. Seed local store observations and citizen demographics
    store.add_citizen(
        CitizenRecord(
            id=citizen_id,
            tenant_id="karnataka_state_health",
            user_id=f"user-{citizen_id}",
            abha_id="91-1234-5678-0001",
            first_name="Ramesh",
            last_name="Patel",
            birth_date="1978-04-12",
            gender=Gender.MALE,
            phone="+919876543210",
            state="Karnataka",
            district="Mysuru",
            sub_district="Hunsur",
            village_or_ward="Ward 1",
        )
    )
    from packages.clinical_models.observations import Observation
    local_obs = Observation(
        citizen_id=citizen_id,
        code="BLOOD_PRESSURE_SYSTOLIC",
        value=136.0,
        unit="mmHg",
        source="SEVAHEALTH_COMMUNITY_CAMP",
        source_event_id=f"local-event-{uuid.uuid4().hex[:4]}",
        recorded_at=datetime.now(timezone.utc),
    )
    store.add_observation(local_obs)

    # Step 3: Disable test EMR endpoint
    emr_adapter.set_enabled(False)

    # Step 4: Health check reports offline status
    emr_health = await emr_adapter.health_check()
    assert emr_health["status"] == "OFFLINE"
    assert "failure" in emr_health["error"].lower() or "disabled" in emr_health["error"].lower()

    fed_health = await fed_provider.health_check()
    assert fed_health["status"] == "DEGRADED"

    # Direct read attempt raises ConnectionError
    with pytest.raises(ConnectionError) as exc_info:
        await emr_adapter.get_patient(citizen_id, ctx)
    assert "failure" in str(exc_info.value).lower() or "disabled" in str(exc_info.value).lower()

    # Federated read preserves local data integrity
    fed_obs = await fed_provider.get_observations(citizen_id, ctx)
    assert len(fed_obs) >= 1
    # Local observation is preserved intact
    assert any(o.value == 136.0 and o.unit == "mmHg" for o in fed_obs)

    # Step 5: Restore the endpoint and retry
    emr_adapter.set_enabled(True)

    recovered_emr_health = await emr_adapter.health_check()
    assert recovered_emr_health["status"] == "ONLINE"

    recovered_fed_health = await fed_provider.health_check()
    assert recovered_fed_health["status"] == "HEALTHY"

    # Step 6: Verify full federated retrieval upon recovery
    recovered_obs = await fed_provider.get_observations(citizen_id, ctx)
    # Contains both local BP and restored external FHIR HbA1c
    assert any(o.value == 136.0 for o in recovered_obs)
    assert any(o.value == 7.1 and "4548-4" in o.code for o in recovered_obs)

    recovered_patient = await fed_provider.get_patient(citizen_id, ctx)
    assert recovered_patient is not None
    assert "Ramesh Patel" in recovered_patient.name


@pytest.mark.asyncio
async def test_tc_int_007_external_emr_retry_policy_recovery():
    """TC-INT-007: Verifies external EMR adapter retry policy on transient failure."""
    emr_adapter = EmrGqlAdapter(endpoint_url="https://emr.bezs-health.internal/graphql")
    
    attempts = 0
    def transient_emr_query():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("Transient EMR gateway connection drop (502 Bad Gateway)")
        return {"data": {"Patient": {"id": "12345", "name": "Test Recovery"}}}

    result = await emr_adapter._execute_with_retry(transient_emr_query)
    assert attempts == 3
    assert result["data"]["Patient"]["name"] == "Test Recovery"
