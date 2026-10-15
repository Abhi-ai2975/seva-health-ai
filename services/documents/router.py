"""FastAPI Router for Clinical Document Ingestion Subsystem.

Provides:
- POST /api/v1/documents/ingest (10-Stage Ingestion Pipeline)
- GET  /api/v1/documents/jobs/{job_id} (Job status & extracted entities)
- GET  /api/v1/documents/review-queue (Human review queue)
- POST /api/v1/documents/jobs/{job_id}/human-review (Clinician review submission)
- GET  /api/v1/documents/download/{doc_id} (Download preserved original document)
- GET  /api/v1/documents/{citizen_id} (List documents for citizen)
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Response, status
from pydantic import BaseModel

from packages.auth.jwt import get_current_user_token, TokenPayload, require_roles
from packages.types.enums import UserRole
from services.store import store
from services.documents.models import (
    DocumentIngestionJob,
    HumanReviewCorrection,
    IngestionStatus,
)
from services.documents.engine import ingestion_engine
from packages.observability.metrics import metrics

router = APIRouter(prefix="/documents", tags=["Clinical Document Ingestion"])


class HumanReviewSubmissionPayload(BaseModel):
    corrections: List[HumanReviewCorrection]


@router.post(
    "/ingest",
    response_model=DocumentIngestionJob,
    summary="Upload & Execute 10-Stage Clinical Document Ingestion Pipeline",
)
async def ingest_clinical_document(
    citizen_id: str = Form(..., description="Target citizen ID"),
    title: str = Form("Clinical Document", description="Document title"),
    declared_type: Optional[str] = Form(None, description="Optional document type hint"),
    file: UploadFile = File(..., description="PDF or Image file"),
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Executes:

    Upload -> Virus/File Validation -> Classification -> OCR -> Extraction ->
    Normalization -> Validation -> Clinical Mapping -> Human Review Gate -> Clinical Record.
    """
    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail=f"Citizen '{citizen_id}' not found.")

    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"

    from packages.security.file_validator import SecureFileValidator, FileValidationError
    try:
        safe_filename = SecureFileValidator.sanitize_filename(file.filename or "uploaded_document")
    except FileValidationError as fv_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(fv_err))

    job = await ingestion_engine.ingest_document(
        citizen_id=citizen_id,
        file_content=file_bytes,
        filename=safe_filename,
        content_type=content_type,
        title=title,
        uploader_id=current_user.sub,
        declared_type=declared_type,
    )

    if job.status == IngestionStatus.REJECTED:
        metrics.record_document_pipeline(
            doc_type=str(getattr(job, "document_type", "LAB_REPORT")),
            duration_ms=45.0,
            success=False,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Document rejected: {'; '.join(job.errors)}",
        )

    metrics.record_document_pipeline(
        doc_type=str(getattr(job, "document_type", "LAB_REPORT")),
        duration_ms=45.0,
        success=True,
    )
    return job


@router.post(
    "/ingest-async",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Non-blocking Asynchronous Document Ingestion",
)
async def ingest_clinical_document_async(
    citizen_id: str = Form(..., description="Target citizen ID"),
    title: str = Form("Clinical Document", description="Document title"),
    declared_type: Optional[str] = Form(None, description="Optional document type hint"),
    file: UploadFile = File(..., description="PDF or Image file"),
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Enqueues document OCR & Entity Extraction to background queue without blocking citizen UX."""
    from packages.queue.manager import job_queue_manager
    from packages.queue.models import QueueType, JobPriority

    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail=f"Citizen '{citizen_id}' not found.")

    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"

    from packages.security.file_validator import SecureFileValidator, FileValidationError
    try:
        safe_filename = SecureFileValidator.sanitize_filename(file.filename or "uploaded_document")
    except FileValidationError as fv_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(fv_err))

    job = await job_queue_manager.enqueue(
        queue=QueueType.DOCUMENT_OCR,
        payload={
            "citizen_id": citizen_id,
            "filename": safe_filename,
            "title": title,
            "content_type": content_type,
            "file_size_bytes": len(file_bytes),
            "uploader_id": current_user.sub,
            "declared_type": declared_type,
        },
        priority=JobPriority.HIGH,
        citizen_id=citizen_id,
        tenant_id=current_user.tenant_id,
    )

    return {
        "status": "QUEUED",
        "job_id": job.id,
        "message": "Document accepted for asynchronous OCR and entity extraction.",
        "status_url": f"/api/v1/jobs/{job.id}",
    }


@router.get(
    "/jobs/{job_id}",
    response_model=DocumentIngestionJob,
    summary="Get Status and Extractions for Ingestion Job",
)
async def get_ingestion_job(
    job_id: str,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    job = ingestion_engine.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


@router.get(
    "/review-queue",
    response_model=List[DocumentIngestionJob],
    summary="List Documents Held for Human Review",
)
async def list_human_review_queue(
    citizen_id: Optional[str] = None,
    current_user: TokenPayload = Depends(
        require_roles([UserRole.CLINICIAN, UserRole.PUBLIC_HEALTH_ADMIN, UserRole.SYSTEM_ADMIN])
    ),
):
    """Lists documents with status HUMAN_REVIEW_REQUIRED."""
    return ingestion_engine.list_review_queue(citizen_id=citizen_id)


@router.post(
    "/jobs/{job_id}/human-review",
    response_model=DocumentIngestionJob,
    summary="Submit Clinician Review & Promote Verified Data to Clinical Record",
)
async def submit_human_review(
    job_id: str,
    payload: HumanReviewSubmissionPayload,
    current_user: TokenPayload = Depends(
        require_roles([UserRole.CLINICIAN, UserRole.SYSTEM_ADMIN])
    ),
):
    """Submits APPROVE, CORRECT, or REJECT actions for extracted fields.

    Once verified, the committer promotes observations into the legal medical record.
    """
    job = ingestion_engine.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    reviewer_user = store.get_user(current_user.sub)
    reviewer_name = reviewer_user.full_name if reviewer_user else "Dr. Verified Clinician"

    updated_job = await ingestion_engine.apply_human_review(
        job_id=job_id,
        corrections=payload.corrections,
        reviewer_id=current_user.sub,
        reviewer_name=reviewer_name,
    )
    return updated_job


@router.get(
    "/download/{doc_id:path}",
    summary="Download Preserved Original Document Bytes",
)
async def download_original_document(
    doc_id: str,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Retrieves original preserved raw file from immutable document storage with path traversal protection."""
    from datetime import datetime, timezone
    from packages.security.path_traversal_guard import (
        PathTraversalDetector,
        StorageVaultManager,
        PathTraversalViolationError,
    )

    # 1. Traversal check on doc_id
    is_malicious, cat, desc = PathTraversalDetector.inspect(doc_id)
    if is_malicious:
        store.security_incidents.append({
            "incident_type": "PATH_TRAVERSAL_ATTEMPT",
            "attack_type": cat or "DOC_ID_TRAVERSAL",
            "parameter": "doc_id",
            "payload_snippet": doc_id[:80],
            "action_taken": "BLOCKED_DOC_ID_TRAVERSAL",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unauthorized path traversal sequence rejected in document ID: {desc}",
        )

    file_record = ingestion_engine.get_document_file(doc_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="Document file not found in storage.")

    # 2. Confinement verification on stored path
    try:
        _ = StorageVaultManager.resolve_and_confine(
            file_record["storage_path"],
            citizen_id=file_record.get("citizen_id"),
        )
    except PathTraversalViolationError as pt_err:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pt_err))

    return Response(
        content=file_record["raw_bytes"],
        media_type=file_record["content_type"],
        headers={
            "Content-Disposition": f'inline; filename="{file_record["filename"]}"',
            "X-SHA256-Checksum": file_record["sha256_hash"],
        },
    )


@router.get(
    "/retrieve-by-path",
    summary="Retrieve Preserved Document via Path Reference with Storage Confinement",
)
async def retrieve_document_by_path(
    path: str,
    citizen_id: Optional[str] = None,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Safely retrieves a document file by its storage path, verifying strict confinement to authorized storage."""
    from datetime import datetime, timezone
    from packages.security.path_traversal_guard import (
        PathTraversalDetector,
        StorageVaultManager,
        PathTraversalViolationError,
    )

    # 1. Inspect for traversal patterns
    is_malicious, cat, desc = PathTraversalDetector.inspect(path)
    if is_malicious:
        store.security_incidents.append({
            "incident_type": "PATH_TRAVERSAL_ATTEMPT",
            "attack_type": cat or "PATH_TRAVERSAL",
            "parameter": "path",
            "payload_snippet": path[:80],
            "action_taken": "BLOCKED_TRAVERSAL_RETRIEVAL",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path traversal attempt rejected: {desc}",
        )

    # 2. Confinement check
    effective_citizen = citizen_id
    if current_user.role == UserRole.CITIZEN and not effective_citizen:
        effective_citizen = current_user.sub

    try:
        confined_path = StorageVaultManager.resolve_and_confine(
            untrusted_path=path,
            citizen_id=effective_citizen,
        )
    except PathTraversalViolationError as pt_err:
        store.security_incidents.append({
            "incident_type": "PATH_TRAVERSAL_ATTEMPT",
            "attack_type": "STORAGE_ESCAPE",
            "parameter": "path",
            "payload_snippet": path[:80],
            "action_taken": "CONFINEMENT_ESCAPE_BLOCKED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: {str(pt_err)}",
        )

    # 3. Retrieve from store by matching storage_path
    normalized_path = path.replace("\\", "/")
    matched_record = None
    for rec in store.document_files.values():
        rec_path = rec.get("storage_path", "").replace("\\", "/")
        if rec_path == normalized_path or str(confined_path).endswith(rec.get("filename", "~~~")):
            matched_record = rec
            break

    if not matched_record:
        raise HTTPException(status_code=404, detail="Authorized document not found in storage vault.")

    # Check citizen authorization (cross-citizen IDOR defense)
    if current_user.role == UserRole.CITIZEN and matched_record.get("citizen_id") != current_user.sub:
        raise HTTPException(status_code=403, detail="Unauthorized access to cross-citizen document.")

    return Response(
        content=matched_record["raw_bytes"],
        media_type=matched_record["content_type"],
        headers={
            "Content-Disposition": f'inline; filename="{matched_record["filename"]}"',
            "X-SHA256-Checksum": matched_record["sha256_hash"],
        },
    )


@router.get(
    "/vault/verify-confinement",
    summary="Verify Path Confinement & Confinement Invariant (TC-SEC-003)",
)
async def verify_vault_path_confinement(
    path: str,
    citizen_id: Optional[str] = None,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Verifies whether an untrusted path is confined to the authorized storage root or rejected."""
    from datetime import datetime, timezone
    from packages.security.path_traversal_guard import (
        PathTraversalDetector,
        StorageVaultManager,
        PathTraversalViolationError,
    )

    is_malicious, cat, desc = PathTraversalDetector.inspect(path)
    vault_root = StorageVaultManager.get_vault_root()

    confined = False
    resolved_path_str = None
    rejection_reason = None

    try:
        resolved = StorageVaultManager.resolve_and_confine(path, citizen_id=citizen_id)
        confined = True
        resolved_path_str = str(resolved)
    except PathTraversalViolationError as err:
        confined = False
        rejection_reason = str(err)

    return {
        "input_path": path,
        "vault_root": str(vault_root),
        "is_traversal_detected": is_malicious,
        "detection_category": cat,
        "detection_details": desc,
        "is_confined_to_vault": confined,
        "resolved_path": resolved_path_str,
        "rejection_reason": rejection_reason,
        "confinement_invariant_satisfied": confined and not is_malicious,
    }


# ==============================================================================
# Legacy Endpoints (Maintained for Backward Compatibility)
# ==============================================================================

@router.post("/upload", summary="Legacy Upload Endpoint")
async def upload_medical_document(
    citizen_id: str = Form(...),
    document_type: str = Form("LAB_REPORT"),
    title: str = Form(...),
    file: UploadFile = File(...),
    current_user: TokenPayload = Depends(get_current_user_token),
):
    """Preserved legacy route forwarding to the comprehensive ingestion engine with traversal sanitization."""
    from datetime import datetime, timezone
    from packages.security.path_traversal_guard import (
        PathTraversalDetector,
        StorageVaultManager,
        PathTraversalViolationError,
    )
    from packages.security.file_validator import SecureFileValidator, FileValidationError

    citizen = store.get_citizen(citizen_id)
    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found")

    # Inspect for path traversal
    is_malicious, cat, desc = PathTraversalDetector.inspect(file.filename or "")
    if is_malicious:
        store.security_incidents.append({
            "incident_type": "PATH_TRAVERSAL_ATTEMPT",
            "attack_type": cat or "FILENAME_TRAVERSAL",
            "parameter": "filename",
            "payload_snippet": (file.filename or "")[:80],
            "action_taken": "SANITIZED_AND_CONFINED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    try:
        safe_filename = StorageVaultManager.sanitize_filename(file.filename or "uploaded_file", strict=False)
    except PathTraversalViolationError as pt_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(pt_err))

    content = await file.read()
    job = await ingestion_engine.ingest_document(
        citizen_id=citizen_id,
        file_content=content,
        filename=safe_filename,
        content_type=file.content_type or "application/octet-stream",
        title=title,
        uploader_id=current_user.sub,
        declared_type=document_type,
    )

    doc_meta = store.documents[citizen_id][-1]
    return {
        "status": "SUCCESS",
        "document": doc_meta,
        "job_id": job.job_id,
        "ingestion_status": job.status.value,
        "message": f"Document '{safe_filename}' processed via ingestion pipeline.",
    }


@router.get("/{citizen_id}", summary="List Citizen Documents")
async def list_citizen_documents(
    citizen_id: str,
    current_user: TokenPayload = Depends(get_current_user_token),
):
    return store.documents.get(citizen_id, [])

