"""Federated Clinical Data Provider Service.

Coordinates, queries, deduplicates, and unifies clinical records across
SevaHealth Local, openEHR/EHRbase, bezs-emr-gql (FHIR R4), and bezs-hms.

Security:
- Enforces strict RBAC via ClinicalRBACGuard.
- Strictly blocks arbitrary AI-generated SQL and GraphQL queries via AntiArbitraryQueryGuard.
- Audits every data read attempt via ClinicalAccessAuditLogger.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import asyncio
import structlog

from packages.types.enums import UserRole
from packages.interop.base import ClinicalDataProvider, AccessContext
from packages.interop.models import (
    ClinicalSourceSystem,
    NormalizedPatient,
    NormalizedObservation,
    NormalizedCondition,
    NormalizedMedication,
    NormalizedEncounter,
    NormalizedCarePlan,
    NormalizedClinicalDocument,
    ObservationCategory,
    FederatedClinicalChart,
)
from packages.interop.security import (
    ClinicalRBACGuard,
    AntiArbitraryQueryGuard,
    AccessDeniedError,
    ArbitraryQueryViolationError,
)
from packages.interop.audit import ClinicalAccessAuditLogger
from packages.interop.adapters.local_adapter import SevaHealthLocalAdapter
from packages.interop.adapters.openehr_adapter import OpenEhrEhrBaseAdapter
from packages.interop.adapters.emr_gql_adapter import EmrGqlAdapter
from packages.interop.adapters.hms_adapter import HmsAdapter

logger = structlog.get_logger(__name__)


class FederatedClinicalDataProvider:
    """Federated engine aggregating and normalizing multiple clinical systems."""

    def __init__(
        self,
        local_adapter: Optional[SevaHealthLocalAdapter] = None,
        openehr_adapter: Optional[OpenEhrEhrBaseAdapter] = None,
        emr_gql_adapter: Optional[EmrGqlAdapter] = None,
        hms_adapter: Optional[HmsAdapter] = None,
    ):
        self.local_adapter = local_adapter or SevaHealthLocalAdapter()
        self.openehr_adapter = openehr_adapter or OpenEhrEhrBaseAdapter()
        self.emr_gql_adapter = emr_gql_adapter or EmrGqlAdapter()
        self.hms_adapter = hms_adapter or HmsAdapter()

        self.adapters: List[ClinicalDataProvider] = [
            self.local_adapter,
            self.openehr_adapter,
            self.emr_gql_adapter,
            self.hms_adapter,
        ]

    def _pre_query_guard(self, patient_id: str, context: AccessContext, resource_type: str, **kwargs) -> None:
        """Enforces anti-injection guard and role-based access control."""
        try:
            # 1. Anti-arbitrary query guard
            AntiArbitraryQueryGuard.validate_request_parameters(
                patient_id=patient_id,
                **kwargs
            )
            # 2. RBAC check
            ClinicalRBACGuard.check_read_permission(patient_id=patient_id, context=context)
        except ArbitraryQueryViolationError as aq_err:
            ClinicalAccessAuditLogger.log_access(
                context=context,
                patient_id=patient_id,
                source_system="FEDERATED",
                resource_type=resource_type,
                items_count=0,
                status="BLOCKED_QUERY_VIOLATION",
                error_message=str(aq_err),
            )
            raise
        except AccessDeniedError as ad_err:
            ClinicalAccessAuditLogger.log_access(
                context=context,
                patient_id=patient_id,
                source_system="FEDERATED",
                resource_type=resource_type,
                items_count=0,
                status="DENIED",
                error_message=str(ad_err),
            )
            raise

    async def get_patient(self, patient_id: str, context: AccessContext) -> Optional[NormalizedPatient]:
        """Resolves patient record across federated sources, prioritizing local demographics."""
        self._pre_query_guard(patient_id, context, "Patient")

        # Query local first
        p = await self.local_adapter.get_patient(patient_id, context)
        found_source = self.local_adapter.source_system.value if p else None

        if not p:
            # Try bezs-emr-gql
            try:
                p = await self.emr_gql_adapter.get_patient(patient_id, context)
                if p:
                    found_source = self.emr_gql_adapter.source_system.value
            except Exception as e:
                logger.warning("emr_gql_patient_lookup_failed", error=str(e), patient_id=patient_id)

        if not p:
            # Try bezs-hms
            try:
                p = await self.hms_adapter.get_patient(patient_id, context)
                if p:
                    found_source = self.hms_adapter.source_system.value
            except Exception as e:
                logger.warning("hms_patient_lookup_failed", error=str(e), patient_id=patient_id)

        if not p:
            # Fallback openehr
            try:
                p = await self.openehr_adapter.get_patient(patient_id, context)
                if p:
                    found_source = self.openehr_adapter.source_system.value
            except Exception as e:
                logger.warning("openehr_patient_lookup_failed", error=str(e), patient_id=patient_id)

        ClinicalAccessAuditLogger.log_access(
            context=context,
            patient_id=patient_id,
            source_system=found_source or "FEDERATED",
            resource_type="Patient",
            items_count=1 if p else 0,
            status="GRANTED",
        )
        return p

    async def get_observations(
        self,
        patient_id: str,
        context: AccessContext,
        category: Optional[ObservationCategory] = None,
    ) -> List[NormalizedObservation]:
        """Federates observations across all sources, deduplicating matching records."""
        self._pre_query_guard(
            patient_id, context, "Observation",
            category=category.value if category else None
        )

        tasks = [adapter.get_observations(patient_id, context, category=category) for adapter in self.adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: List[NormalizedObservation] = []
        for res in results:
            if isinstance(res, list):
                merged.extend(res)

        # Deduplicate by code and approximate timestamp
        seen = set()
        deduped: List[NormalizedObservation] = []
        for obs in merged:
            dt_key = obs.effective_datetime.strftime("%Y-%m-%d-%H")
            key = (obs.code, dt_key, round(obs.value, 2))
            if key not in seen:
                seen.add(key)
                deduped.append(obs)

        # Sort newest first
        deduped.sort(key=lambda o: o.effective_datetime, reverse=True)

        ClinicalAccessAuditLogger.log_access(
            context=context,
            patient_id=patient_id,
            source_system="FEDERATED",
            resource_type="Observation",
            items_count=len(deduped),
            status="GRANTED",
        )
        return deduped

    async def get_conditions(self, patient_id: str, context: AccessContext) -> List[NormalizedCondition]:
        """Federates active conditions and diagnoses, deduplicating matching codes."""
        self._pre_query_guard(patient_id, context, "Condition")

        tasks = [adapter.get_conditions(patient_id, context) for adapter in self.adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: List[NormalizedCondition] = []
        for res in results:
            if isinstance(res, list):
                merged.extend(res)

        # Deduplicate by code or condition name
        seen = set()
        deduped: List[NormalizedCondition] = []
        for c in merged:
            key = (c.code, c.display_name.lower())
            if key not in seen:
                seen.add(key)
                deduped.append(c)

        ClinicalAccessAuditLogger.log_access(
            context=context,
            patient_id=patient_id,
            source_system="FEDERATED",
            resource_type="Condition",
            items_count=len(deduped),
            status="GRANTED",
        )
        return deduped

    async def get_medications(self, patient_id: str, context: AccessContext) -> List[NormalizedMedication]:
        """Federates medications across sources, deduplicating identical prescription titles."""
        self._pre_query_guard(patient_id, context, "Medication")

        tasks = [adapter.get_medications(patient_id, context) for adapter in self.adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: List[NormalizedMedication] = []
        for res in results:
            if isinstance(res, list):
                merged.extend(res)

        # Deduplicate
        seen = set()
        deduped: List[NormalizedMedication] = []
        for m in merged:
            norm_name = m.medication_name.strip().lower()
            if norm_name not in seen:
                seen.add(norm_name)
                deduped.append(m)

        ClinicalAccessAuditLogger.log_access(
            context=context,
            patient_id=patient_id,
            source_system="FEDERATED",
            resource_type="Medication",
            items_count=len(deduped),
            status="GRANTED",
        )
        return deduped

    async def get_encounters(self, patient_id: str, context: AccessContext) -> List[NormalizedEncounter]:
        """Federates physical visits, virtual video calls (HMS), and FHIR encounters."""
        self._pre_query_guard(patient_id, context, "Encounter")

        tasks = [adapter.get_encounters(patient_id, context) for adapter in self.adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: List[NormalizedEncounter] = []
        for res in results:
            if isinstance(res, list):
                merged.extend(res)

        # Sort chronologically (newest first)
        merged.sort(key=lambda e: e.period_start, reverse=True)

        ClinicalAccessAuditLogger.log_access(
            context=context,
            patient_id=patient_id,
            source_system="FEDERATED",
            resource_type="Encounter",
            items_count=len(merged),
            status="GRANTED",
        )
        return merged

    async def get_care_plans(self, patient_id: str, context: AccessContext) -> List[NormalizedCarePlan]:
        """Federates prevention care plans and therapeutic protocols."""
        self._pre_query_guard(patient_id, context, "CarePlan")

        tasks = [adapter.get_care_plans(patient_id, context) for adapter in self.adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: List[NormalizedCarePlan] = []
        for res in results:
            if isinstance(res, list):
                merged.extend(res)

        ClinicalAccessAuditLogger.log_access(
            context=context,
            patient_id=patient_id,
            source_system="FEDERATED",
            resource_type="CarePlan",
            items_count=len(merged),
            status="GRANTED",
        )
        return merged

    async def get_clinical_documents(
        self, patient_id: str, context: AccessContext
    ) -> List[NormalizedClinicalDocument]:
        """Federates documents, SOAP reports, and intake assessments."""
        self._pre_query_guard(patient_id, context, "ClinicalDocument")

        tasks = [adapter.get_clinical_documents(patient_id, context) for adapter in self.adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: List[NormalizedClinicalDocument] = []
        for res in results:
            if isinstance(res, list):
                merged.extend(res)

        # Sort newest first
        merged.sort(key=lambda d: d.created_at, reverse=True)

        ClinicalAccessAuditLogger.log_access(
            context=context,
            patient_id=patient_id,
            source_system="FEDERATED",
            resource_type="ClinicalDocument",
            items_count=len(merged),
            status="GRANTED",
        )
        return merged

    async def get_federated_chart(self, patient_id: str, context: AccessContext) -> FederatedClinicalChart:
        """Aggregates an entire longitudinal clinical chart across all connected EMRs."""
        self._pre_query_guard(patient_id, context, "FederatedClinicalChart")

        # Concurrently gather all facets
        patient, observations, conditions, medications, encounters, care_plans, documents = await asyncio.gather(
            self.get_patient(patient_id, context),
            self.get_observations(patient_id, context),
            self.get_conditions(patient_id, context),
            self.get_medications(patient_id, context),
            self.get_encounters(patient_id, context),
            self.get_care_plans(patient_id, context),
            self.get_clinical_documents(patient_id, context),
        )

        return FederatedClinicalChart(
            patient=patient,
            patient_id=patient_id,
            observations=observations,
            conditions=conditions,
            medications=medications,
            encounters=encounters,
            care_plans=care_plans,
            documents=documents,
            source_systems_queried=[a.source_system for a in self.adapters],
            generated_at=datetime.now(timezone.utc),
        )

    async def health_check(self) -> Dict[str, Any]:
        """Aggregated connectivity and status of all 4 adapters."""
        checks = await asyncio.gather(
            self.local_adapter.health_check(),
            self.openehr_adapter.health_check(),
            self.emr_gql_adapter.health_check(),
            self.hms_adapter.health_check(),
            return_exceptions=True,
        )

        adapters_status = [
            res if isinstance(res, dict) else {"error": str(res), "status": "ERROR"}
            for res in checks
        ]
        has_offline = any(
            isinstance(a, dict) and a.get("status") in ["OFFLINE", "ERROR"]
            for a in adapters_status
        )

        return {
            "status": "DEGRADED" if has_offline else "HEALTHY",
            "adapters": adapters_status,
            "security": {
                "rbac_enforcement": "ACTIVE",
                "anti_arbitrary_query_guard": "ACTIVE",
                "audit_logging": "ACTIVE",
            }
        }


# Global singleton federated provider
federated_clinical_data_provider = FederatedClinicalDataProvider()
