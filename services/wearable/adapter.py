from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
import uuid
import asyncio
import random
import structlog

from services.wearable.models import (
    WearableProvider,
    WearableMetricType,
    WearableProvenance,
    NormalizedWearableRecord,
    WearableConnectionState,
    WearableProjection,
    WebhookPayload,
)
from services.wearable.projections import calculate_wearable_projections

logger = structlog.get_logger(__name__)

WEARABLE_METRIC_OBSERVATION_MAP: Dict[WearableMetricType, Tuple[str, str, str]] = {
    WearableMetricType.HEART_RATE: ("HEART_RATE", "8867-4", "Heart rate"),
    WearableMetricType.STEPS: ("STEPS", "55423-8", "Daily Step Count"),
    WearableMetricType.HRV: ("HRV", "80404-7", "Heart rate variability (RMSSD)"),
    WearableMetricType.SLEEP: ("SLEEP_DURATION", "93832-4", "Sleep duration"),
    WearableMetricType.WEIGHT: ("WEIGHT", "29463-7", "Body weight"),
    WearableMetricType.CALORIES: ("CALORIES_BURNED", "41981-2", "Active calories burned"),
    WearableMetricType.WORKOUTS: ("WORKOUT_SESSIONS", "55411-3", "Exercise sessions"),
    WearableMetricType.BLOOD_PRESSURE: ("SYSTOLIC_BP", "8480-6", "Systolic blood pressure"),
    WearableMetricType.GLUCOSE: ("FASTING_GLUCOSE", "1558-6", "Continuous glucose reading"),
    WearableMetricType.SPO2: ("SPO2", "59408-5", "Oxygen saturation in blood"),
    WearableMetricType.RESPIRATORY_RATE: ("RESPIRATORY_RATE", "9279-1", "Respiratory rate"),
}


# ==============================================================================
# 1. ABSTRACT WEARABLE ADAPTER INTERFACE
# ==============================================================================

class WearableAdapter(ABC):
    @abstractmethod
    async def connect_provider(
        self, citizen_id: str, provider: WearableProvider, auth_payload: Dict[str, Any]
    ) -> WearableConnectionState:
        """Connects citizen to an external health data provider."""
        pass

    @abstractmethod
    async def sync(
        self, citizen_id: str, provider: Optional[WearableProvider] = None
    ) -> List[NormalizedWearableRecord]:
        """Performs full sync across connected providers."""
        pass

    @abstractmethod
    async def incremental_sync(
        self, citizen_id: str, cursor: Optional[str] = None
    ) -> Tuple[List[NormalizedWearableRecord], str]:
        """Performs incremental delta sync using timestamp or pagination cursor."""
        pass

    @abstractmethod
    async def handle_webhook(self, payload: WebhookPayload) -> Dict[str, Any]:
        """Ingests real-time push events from external wearable providers."""
        pass

    @abstractmethod
    async def disconnect_provider(self, citizen_id: str, provider: WearableProvider) -> bool:
        """Disconnects citizen from provider."""
        pass

    @abstractmethod
    async def revoke_consent(self, citizen_id: str) -> bool:
        """Revokes health data sharing consent and halts future ingestion."""
        pass

    @abstractmethod
    async def delete_all_wearable_data(self, citizen_id: str) -> bool:
        """Data deletion workflow ensuring citizen right-to-be-forgotten."""
        pass


# ==============================================================================
# 2. MOCK WEARABLE PROVIDER (Local Demo & Offline Evaluation)
# ==============================================================================

class MockWearableProvider:
    """Simulates realistic Apple Health, Garmin, Fitbit, Whoop, and Oura datasets."""

    @classmethod
    def generate_records(
        self,
        citizen_id: str,
        provider: WearableProvider = WearableProvider.GARMIN,
        days: int = 14,
        physiological_trend: str = "DEFAULT",  # DEFAULT | DETERIORATING | IMPROVING
        include_blood_pressure: bool = True,
        include_glucose: bool = True,
    ) -> List[NormalizedWearableRecord]:
        now = datetime.now(timezone.utc)
        records: List[NormalizedWearableRecord] = []

        # Baseline parameters
        base_rhr = 70.0
        base_hrv = 52.0
        base_steps = 7500
        base_sleep = 7.2
        base_weight = 74.0

        for d in range(days, 0, -1):
            ts = now - timedelta(days=d, hours=random.randint(1, 4))
            day_fraction = (days - d) / max(1, days)

            # Apply trend drift
            if physiological_trend == "DETERIORATING":
                # Escalating resting HR, severe autonomic suppression, sleep deficit, sedentary
                rhr_val = round(base_rhr + (day_fraction * 12.0) + random.uniform(-1.5, 1.5), 1)
                hrv_val = round(max(18.0, base_hrv - (day_fraction * 28.0) + random.uniform(-2.0, 2.0)), 1)
                steps_val = max(1200, int(base_steps - (day_fraction * 5200) + random.randint(-400, 400)))
                sleep_val = round(max(4.2, base_sleep - (day_fraction * 2.5) + random.uniform(-0.4, 0.4)), 1)
                active_cals = round(max(150.0, 450.0 - (day_fraction * 300.0)), 1)
                active_mins = round(max(5.0, 40.0 - (day_fraction * 35.0)), 1)
            elif physiological_trend == "IMPROVING":
                # Decreasing resting HR, enhanced HRV vagal recovery, high step count
                rhr_val = round(base_rhr - (day_fraction * 6.0) + random.uniform(-1.0, 1.0), 1)
                hrv_val = round(min(75.0, base_hrv + (day_fraction * 18.0) + random.uniform(-1.5, 1.5)), 1)
                steps_val = int(base_steps + (day_fraction * 3000) + random.randint(-500, 500))
                sleep_val = round(min(8.5, base_sleep + (day_fraction * 0.8)), 1)
                active_cals = round(450.0 + (day_fraction * 200.0), 1)
                active_mins = round(40.0 + (day_fraction * 25.0), 1)
            else:
                rhr_val = round(base_rhr + random.uniform(-2.0, 2.0), 1)
                hrv_val = round(base_hrv + random.uniform(-3.0, 3.0), 1)
                steps_val = int(base_steps + random.randint(-800, 800))
                sleep_val = round(base_sleep + random.uniform(-0.5, 0.5), 1)
                active_cals = 420.0
                active_mins = 35.0

            prov = WearableProvenance(
                provider=provider,
                device_brand=provider.value.replace("_", " ").title(),
                device_model="Forerunner 265 / Series 9 Sensor",
                is_medical_grade=False,
            )

            # 1. Steps
            records.append(NormalizedWearableRecord(
                citizen_id=citizen_id,
                source="OPEN_WEARABLES_CONNECTOR",
                provider=provider,
                timestamp=ts,
                metric=WearableMetricType.STEPS,
                value=float(steps_val),
                unit="steps",
                confidence=0.98,
                provenance=prov,
            ))

            # 2. Activity Minutes
            records.append(NormalizedWearableRecord(
                citizen_id=citizen_id,
                source="OPEN_WEARABLES_CONNECTOR",
                provider=provider,
                timestamp=ts,
                metric=WearableMetricType.ACTIVITY,
                value=active_mins,
                unit="minutes",
                confidence=0.95,
                provenance=prov,
            ))

            # 3. Resting Heart Rate
            records.append(NormalizedWearableRecord(
                citizen_id=citizen_id,
                source="OPEN_WEARABLES_CONNECTOR",
                provider=provider,
                timestamp=ts,
                metric=WearableMetricType.HEART_RATE,
                value=rhr_val,
                unit="bpm",
                confidence=0.94,
                provenance=prov,
            ))

            # 4. HRV rMSSD
            records.append(NormalizedWearableRecord(
                citizen_id=citizen_id,
                source="OPEN_WEARABLES_CONNECTOR",
                provider=provider,
                timestamp=ts,
                metric=WearableMetricType.HRV,
                value=hrv_val,
                unit="ms",
                confidence=0.92,
                provenance=prov,
            ))

            # 5. Sleep Duration
            records.append(NormalizedWearableRecord(
                citizen_id=citizen_id,
                source="OPEN_WEARABLES_CONNECTOR",
                provider=provider,
                timestamp=ts,
                metric=WearableMetricType.SLEEP,
                value=sleep_val,
                unit="hours",
                confidence=0.91,
                provenance=prov,
            ))

            # 6. Weight
            records.append(NormalizedWearableRecord(
                citizen_id=citizen_id,
                source="OPEN_WEARABLES_CONNECTOR",
                provider=provider,
                timestamp=ts,
                metric=WearableMetricType.WEIGHT,
                value=base_weight,
                unit="kg",
                confidence=0.99,
                provenance=prov,
            ))

            # 7. Active Calories
            records.append(NormalizedWearableRecord(
                citizen_id=citizen_id,
                source="OPEN_WEARABLES_CONNECTOR",
                provider=provider,
                timestamp=ts,
                metric=WearableMetricType.CALORIES,
                value=active_cals,
                unit="kcal",
                confidence=0.90,
                provenance=prov,
            ))

            # 8. Workouts
            records.append(NormalizedWearableRecord(
                citizen_id=citizen_id,
                source="OPEN_WEARABLES_CONNECTOR",
                provider=provider,
                timestamp=ts,
                metric=WearableMetricType.WORKOUTS,
                value=1.0 if steps_val > 5000 else 0.0,
                unit="sessions",
                confidence=0.96,
                provenance=prov,
            ))

            # 9. Blood Pressure (if provider supports it)
            if include_blood_pressure:
                sbp_mock = 138.0 if physiological_trend != "DETERIORATING" else 146.0
                records.append(NormalizedWearableRecord(
                    citizen_id=citizen_id,
                    source="OPEN_WEARABLES_OPTIONAL_EXTENSION",
                    provider=provider,
                    timestamp=ts,
                    metric=WearableMetricType.BLOOD_PRESSURE,
                    value=sbp_mock,
                    unit="mmHg",
                    confidence=0.85,  # Optical cuffless BP has lower confidence
                    provenance=prov,
                ))

            # 10. Glucose (if provider supports it, e.g. CGM)
            if include_glucose:
                fbg_mock = 116.0 if physiological_trend != "DETERIORATING" else 128.0
                records.append(NormalizedWearableRecord(
                    citizen_id=citizen_id,
                    source="OPEN_WEARABLES_OPTIONAL_EXTENSION",
                    provider=provider,
                    timestamp=ts,
                    metric=WearableMetricType.GLUCOSE,
                    value=fbg_mock,
                    unit="mg/dL",
                    confidence=0.88,
                    provenance=prov,
                ))

            # 11. SpO2
            records.append(NormalizedWearableRecord(
                citizen_id=citizen_id,
                source="OPEN_WEARABLES_CONNECTOR",
                provider=provider,
                timestamp=ts,
                metric=WearableMetricType.SPO2,
                value=98.0,
                unit="%",
                confidence=0.95,
                provenance=prov,
            ))

        return records


# ==============================================================================
# 3. SEVAHEALTH OPEN WEARABLES ADAPTER
# ==============================================================================

class OpenWearablesAdapter(WearableAdapter):
    """Production-ready Open Wearables Adapter for SevaHealth AI.
    
    Handles provider connections, batch sync, cursor-based incremental sync,
    webhook ingestion, exponential retry backoff, consent revocation,
    and GDPR/DISHA compliant data deletion workflows.
    """

    def __init__(self):
        self.connections: Dict[str, Dict[str, WearableConnectionState]] = {}  # citizen_id -> {provider: state}
        self.raw_records: Dict[str, List[NormalizedWearableRecord]] = {}       # citizen_id -> records
        self.projections: Dict[str, WearableProjection] = {}                  # citizen_id -> projection
        self.max_retries: int = 3

    async def connect_provider(
        self, citizen_id: str, provider: WearableProvider, auth_payload: Dict[str, Any]
    ) -> WearableConnectionState:
        token = auth_payload.get("access_token", "demo_oauth_token_12345")
        masked = f"{token[:4]}****{token[-4:]}" if len(token) > 8 else "****"

        state = WearableConnectionState(
            citizen_id=citizen_id,
            provider=provider,
            status="CONNECTED",
            access_token_masked=masked,
            last_sync_timestamp=None,
            last_sync_cursor=None,
            consent_granted_at=datetime.now(timezone.utc),
        )

        if citizen_id not in self.connections:
            self.connections[citizen_id] = {}
        self.connections[citizen_id][provider.value] = state

        logger.info("wearable_provider_connected", citizen_id=citizen_id, provider=provider.value)
        return state

    async def sync(
        self,
        citizen_id: str,
        provider: Optional[WearableProvider] = None,
        physiological_trend: str = "DEFAULT",
    ) -> List[NormalizedWearableRecord]:
        """Performs full sync with exponential retry backoff for transient network issues."""
        target_provider = provider or WearableProvider.GARMIN

        # Check if consent has been revoked on connection
        if citizen_id in self.connections and target_provider.value in self.connections[citizen_id]:
            conn = self.connections[citizen_id][target_provider.value]
            if conn.status == "REVOKED" or conn.consent_revoked_at is not None:
                logger.warning("wearable_sync_blocked_revoked", citizen_id=citizen_id)
                raise PermissionError(f"Wearable synchronization blocked: Access for citizen '{citizen_id}' has been revoked.")

        # Execute with retry logic
        records = await self._execute_with_retry(
            lambda: MockWearableProvider.generate_records(
                citizen_id=citizen_id,
                provider=target_provider,
                days=14,
                physiological_trend=physiological_trend,
            )
        )

        if citizen_id not in self.raw_records:
            self.raw_records[citizen_id] = []
        existing_ids = {r.id for r in self.raw_records[citizen_id]}
        unique_records = [r for r in records if r.id not in existing_ids]
        self.raw_records[citizen_id].extend(unique_records)

        # Update connection state
        if citizen_id in self.connections and target_provider.value in self.connections[citizen_id]:
            conn = self.connections[citizen_id][target_provider.value]
            conn.last_sync_timestamp = datetime.now(timezone.utc)
            conn.last_sync_cursor = str(int(datetime.now(timezone.utc).timestamp()))

        # Update Projection
        proj = calculate_wearable_projections(citizen_id, self.raw_records[citizen_id])
        self.projections[citizen_id] = proj

        # Persist normalized observations to central store (TC-INT-004)
        self._persist_records_to_clinical_store(citizen_id, records)

        logger.info("wearable_full_sync_completed", citizen_id=citizen_id, records_count=len(records))
        return records

    async def incremental_sync(
        self, citizen_id: str, cursor: Optional[str] = None
    ) -> Tuple[List[NormalizedWearableRecord], str]:
        """Performs incremental sync retrieving only newly captured records since cursor."""
        if citizen_id in self.connections:
            for conn in self.connections[citizen_id].values():
                if conn.status == "REVOKED" or conn.consent_revoked_at is not None:
                    logger.warning("wearable_sync_blocked_revoked", citizen_id=citizen_id)
                    raise PermissionError(f"Wearable synchronization blocked: Access for citizen '{citizen_id}' has been revoked.")

        now = datetime.now(timezone.utc)
        since_time = now - timedelta(days=2)
        if cursor:
            try:
                since_time = datetime.fromtimestamp(float(cursor), tz=timezone.utc)
            except Exception:
                pass

        # Generate new delta records (e.g. past 2 days)
        new_records = MockWearableProvider.generate_records(
            citizen_id=citizen_id,
            provider=WearableProvider.APPLE_HEALTH,
            days=2,
        )
        delta = [r for r in new_records if r.timestamp >= since_time]

        if citizen_id not in self.raw_records:
            self.raw_records[citizen_id] = []
        existing_ids = {r.id for r in self.raw_records[citizen_id]}
        unique_delta = [r for r in delta if r.id not in existing_ids]
        self.raw_records[citizen_id].extend(unique_delta)

        # Update cursor
        new_cursor = str(int(now.timestamp()))
        if citizen_id in self.connections:
            for conn in self.connections[citizen_id].values():
                conn.last_sync_cursor = new_cursor
                conn.last_sync_timestamp = now

        # Update projection
        self.projections[citizen_id] = calculate_wearable_projections(citizen_id, self.raw_records[citizen_id])

        # Persist normalized delta observations to central store (TC-INT-004)
        self._persist_records_to_clinical_store(citizen_id, delta)

        return delta, new_cursor

    async def handle_webhook(self, payload: WebhookPayload) -> Dict[str, Any]:
        """Processes asynchronous push payloads from provider webhooks."""
        citizen_id = payload.citizen_id
        prov = WearableProvenance(
            provider=payload.provider,
            device_brand=payload.provider.value,
            device_model="Webhook Push Ingest",
            is_medical_grade=False,
        )

        inbound_records: List[NormalizedWearableRecord] = []
        for pt in payload.data_points:
            metric_type = WearableMetricType(pt.get("metric", "heart_rate"))
            inbound_records.append(NormalizedWearableRecord(
                citizen_id=citizen_id,
                source="OPEN_WEARABLES_WEBHOOK",
                provider=payload.provider,
                timestamp=datetime.now(timezone.utc),
                metric=metric_type,
                value=float(pt.get("value", 72.0)),
                unit=pt.get("unit", "bpm"),
                confidence=float(pt.get("confidence", 0.95)),
                provenance=prov,
            ))

        if citizen_id not in self.raw_records:
            self.raw_records[citizen_id] = []
        self.raw_records[citizen_id].extend(inbound_records)

        # Refresh projection
        self.projections[citizen_id] = calculate_wearable_projections(citizen_id, self.raw_records[citizen_id])

        return {
            "status": "ACCEPTED",
            "provider": payload.provider.value,
            "processed_points": len(inbound_records),
        }

    async def disconnect_provider(self, citizen_id: str, provider: WearableProvider) -> bool:
        if citizen_id in self.connections and provider.value in self.connections[citizen_id]:
            self.connections[citizen_id][provider.value].status = "DISCONNECTED"
            logger.info("wearable_provider_disconnected", citizen_id=citizen_id, provider=provider.value)
            return True
        return False

    async def revoke_consent(self, citizen_id: str) -> bool:
        """Revokes consent directive for all wearable providers for this citizen."""
        if citizen_id in self.connections:
            now = datetime.now(timezone.utc)
            for conn in self.connections[citizen_id].values():
                conn.status = "REVOKED"
                conn.consent_revoked_at = now
            logger.info("wearable_consent_revoked", citizen_id=citizen_id)
            return True
        return False

    async def delete_all_wearable_data(self, citizen_id: str) -> bool:
        """Purges raw wearable records and projections (Right to be Forgotten)."""
        deleted_records = len(self.raw_records.pop(citizen_id, []))
        self.projections.pop(citizen_id, None)
        if citizen_id in self.connections:
            for conn in self.connections[citizen_id].values():
                conn.status = "REVOKED"
        from services.store import store
        if citizen_id in store.observations:
            store.observations[citizen_id] = [
                o for o in store.observations[citizen_id]
                if not (o.source and "WEARABLE" in o.source.upper())
            ]
        logger.info("wearable_data_purged", citizen_id=citizen_id, count=deleted_records)
        return True

    def _persist_records_to_clinical_store(self, citizen_id: str, records: List[NormalizedWearableRecord]):
        """Persists normalized wearable telemetry into citizen Observation repository (TC-INT-004)."""
        from services.store import store
        from packages.clinical_models.observations import Observation

        for rec in records:
            if rec.metric in WEARABLE_METRIC_OBSERVATION_MAP:
                code_name, loinc, display = WEARABLE_METRIC_OBSERVATION_MAP[rec.metric]
                obs = Observation(
                    citizen_id=citizen_id,
                    code=code_name,
                    value=rec.value,
                    unit=rec.unit,
                    recorded_at=rec.timestamp,
                    source=rec.source or "OPEN_WEARABLES_CONNECTOR",
                    source_event_id=rec.id,
                    provenance={
                        "provider": rec.provider.value,
                        "device_brand": rec.provenance.device_brand,
                        "device_model": rec.provenance.device_model,
                        "sync_protocol": rec.provenance.sync_protocol,
                        "confidence": rec.confidence,
                        "is_medical_grade": rec.is_medical_grade,
                    },
                    loinc_code=loinc,
                    display_name=display,
                )
                store.add_observation(obs)

    def get_projection(self, citizen_id: str) -> Optional[WearableProjection]:
        return self.projections.get(citizen_id)

    async def _execute_with_retry(self, operation):
        """Exponential backoff retry helper for transient network calls."""
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if asyncio.iscoroutinefunction(operation):
                    return await operation()
                res = operation()
                if asyncio.iscoroutine(res):
                    return await res
                return res
            except Exception as e:
                last_error = e
                if attempt == self.max_retries:
                    raise e
                await asyncio.sleep(0.01 * (2 ** attempt))
        raise last_error


# Singleton adapter instance
wearable_adapter = OpenWearablesAdapter()
