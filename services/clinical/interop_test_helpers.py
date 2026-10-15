import io
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from services.store import store, CitizenRecord
from packages.types.enums import Gender, UserRole
from services.documents.models import IngestionStatus, HumanReviewCorrection
from services.documents.engine import ingestion_engine
from services.wearable.models import WearableProvider, WearableMetricType, WearableProvenance, NormalizedWearableRecord
from services.wearable.adapter import OpenWearablesAdapter, MockWearableProvider, wearable_adapter
from packages.clinical_models.repository import OpenEHRRepository
from packages.clinical_models.openehr_builder import build_vital_signs_composition
from packages.clinical_models.domain_models import VitalSign, ProvenanceRecord
from packages.interop.adapters.emr_gql_adapter import EmrGqlAdapter
from packages.interop.adapters.local_adapter import SevaHealthLocalAdapter
from packages.interop.adapters.openehr_adapter import OpenEhrEhrBaseAdapter
from packages.clinical_models.observations import Observation
from packages.interop.service import FederatedClinicalDataProvider


def _build_test_pdf(text_lines: list) -> bytes:
    escaped = "\n".join(text_lines).replace("(", "\\(").replace(")", "\\)")
    stream_content = f"BT /F1 12 Tf 72 712 Td ({escaped}) Tj ET"
    stream_bytes = stream_content.encode("latin-1")
    return (
        b"%PDF-1.4\n"
        b"1 0 obj <</Type /Catalog /Pages 2 0 R>> endobj\n"
        b"2 0 obj <</Type /Pages /Kids [3 0 R] /Count 1>> endobj\n"
        b"3 0 obj <</Type /Page /Parent 2 0 R /Resources <</Font <</F1 <</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>>>>> /MediaBox [0 0 612 792] /Contents 4 0 R>> endobj\n"
        b"4 0 obj <</Length " + str(len(stream_bytes)).encode() + b">> stream\n"
        + stream_bytes +
        b"\nendstream\nendobj\nxref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000281 00000 n \ntrailer <</Size 5 /Root 1 0 R>>\nstartxref\n380\n%%EOF"
    )
