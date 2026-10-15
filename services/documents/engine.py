"""Clinical Document Ingestion Engine.

Coordinates the 10-stage clinical document ingestion pipeline:
1. UPLOAD (SHA-256 generation & immutable raw document preservation)
2. VIRUS / FILE VALIDATION
3. DOCUMENT CLASSIFICATION
4. OCR / TEXT EXTRACTION
5. CLINICAL ENTITY EXTRACTION
6. NORMALIZATION
7. VALIDATION
8. CLINICAL MAPPING
9. HUMAN REVIEW GATE
10. CLINICAL RECORD COMMITTER
"""

import hashlib
import uuid
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from services.documents.models import (
    DocumentType,
    IngestionStatus,
    FieldReviewStatus,
    DocumentMetadata,
    DocumentIngestionJob,
    ClinicalExtractionBundle,
    HumanReviewCorrection,
)
from services.documents.pipeline import (
    PipelineContext,
    VirusAndFileValidatorStage,
    DocumentClassificationStage,
    OCRExtractionStage,
    ClinicalEntityExtractorStage,
    ClinicalNormalizerStage,
    ClinicalValidatorStage,
    ClinicalMapperStage,
    HumanReviewGateStage,
    ClinicalRecordCommitterStage,
)
from services.store import store


class ClinicalDocumentIngestionEngine:
    """Master orchestrator for multi-stage clinical document processing."""

    def __init__(self):
        self.virus_stage = VirusAndFileValidatorStage()
        self.classifier_stage = DocumentClassificationStage()
        self.ocr_stage = OCRExtractionStage()
        self.extractor_stage = ClinicalEntityExtractorStage()
        self.normalizer_stage = ClinicalNormalizerStage()
        self.validator_stage = ClinicalValidatorStage()
        self.mapper_stage = ClinicalMapperStage()
        self.gate_stage = HumanReviewGateStage()
        self.committer_stage = ClinicalRecordCommitterStage()

    async def ingest_document(
        self,
        citizen_id: str,
        file_content: bytes,
        filename: str,
        content_type: str,
        title: str,
        uploader_id: str,
        declared_type: Optional[str] = None,
        mock_ocr_text: Optional[str] = None,
    ) -> DocumentIngestionJob:
        """Executes the full 10-stage ingestion pipeline with strict quality gates."""

        # 1. Preservation & Hash Computation (Stage 1: UPLOAD)
        from packages.security.path_traversal_guard import StorageVaultManager
        doc_id = f"doc-{uuid.uuid4().hex[:12]}"
        sha256_hash = hashlib.sha256(file_content).hexdigest()
        clean_filename = StorageVaultManager.sanitize_filename(filename, strict=False)
        storage_path, physical_path = StorageVaultManager.get_safe_storage_path(citizen_id, doc_id, clean_filename)

        # Immutable Preservation of Original File
        store.document_files[doc_id] = {
            "document_id": doc_id,
            "citizen_id": citizen_id,
            "filename": clean_filename,
            "content_type": content_type,
            "size_bytes": len(file_content),
            "sha256_hash": sha256_hash,
            "storage_path": storage_path,
            "physical_path": str(physical_path),
            "raw_bytes": file_content,
            "uploaded_by": uploader_id,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }

        metadata = DocumentMetadata(
            document_id=doc_id,
            citizen_id=citizen_id,
            title=title or clean_filename,
            filename=clean_filename,
            content_type=content_type,
            size_bytes=len(file_content),
            sha256_hash=sha256_hash,
            storage_path=storage_path,
            uploaded_by=uploader_id,
        )

        # Register metadata in store.documents
        if citizen_id not in store.documents:
            store.documents[citizen_id] = []
        store.documents[citizen_id].append({
            "id": doc_id,
            "citizen_id": citizen_id,
            "title": title or clean_filename,
            "filename": clean_filename,
            "content_type": content_type,
            "size_bytes": len(file_content),
            "sha256_hash": sha256_hash,
            "uploaded_by": uploader_id,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "storage_url": f"/api/v1/documents/download/{doc_id}",
        })

        # Initialize Job
        job = DocumentIngestionJob(
            document_id=doc_id,
            citizen_id=citizen_id,
            status=IngestionStatus.UPLOADED,
        )

        stage_data = {}
        if mock_ocr_text:
            stage_data["mock_ocr_text"] = mock_ocr_text

        ctx = PipelineContext(
            metadata=metadata,
            raw_content=file_content,
            job=job,
            stage_data=stage_data,
        )

        # 2. Virus & File Validation
        v_res = await self.virus_stage.process(ctx)
        if not v_res.success:
            self._save_job(job)
            return job

        # 3. OCR Text Extraction
        ocr_res = await self.ocr_stage.process(ctx)
        if not ocr_res.success:
            self._save_job(job)
            return job

        # 4. Document Classification
        await self.classifier_stage.process(ctx)

        # 5. Clinical Entity Extraction
        await self.extractor_stage.process(ctx)

        # 6. Normalization
        await self.normalizer_stage.process(ctx)

        # 7. Clinical Validation
        await self.validator_stage.process(ctx)

        # 8. Clinical Terminology Mapping
        await self.mapper_stage.process(ctx)

        # 9. Human Review Gate
        await self.gate_stage.process(ctx)

        # 10. Clinical Record Committer (Guarded)
        await self.committer_stage.process(ctx)

        job.updated_at = datetime.now(timezone.utc)
        self._save_job(job)

        # If human review is required, enqueue into review_queue
        if job.status == IngestionStatus.HUMAN_REVIEW_REQUIRED:
            store.review_queue[job.job_id] = job
        elif job.job_id in store.review_queue:
            store.review_queue.pop(job.job_id, None)

        return job

    async def apply_human_review(
        self,
        job_id: str,
        corrections: List[HumanReviewCorrection],
        reviewer_id: str,
        reviewer_name: str,
    ) -> DocumentIngestionJob:
        """Processes clinician feedback, updates field values, and commits verified records."""

        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Ingestion Job '{job_id}' not found.")

        bundle = job.extractions

        for cor in corrections:
            cat = cor.target_category.upper()
            act = cor.action.upper()  # APPROVE | CORRECT | REJECT

            if cat == "TEST":
                for test in bundle.tests:
                    if test.id == cor.target_id:
                        if act == "APPROVE":
                            test.review_status = FieldReviewStatus.REVIEWER_APPROVED
                            test.confidence = 1.0
                            test.reviewer_comments = cor.reviewer_notes
                        elif act == "CORRECT":
                            test.review_status = FieldReviewStatus.REVIEWER_CORRECTED
                            if cor.corrected_value is not None:
                                test.numeric_value = float(cor.corrected_value)
                                test.raw_value = str(cor.corrected_value)
                            if cor.corrected_unit:
                                test.normalized_unit = cor.corrected_unit
                            if cor.corrected_interpretation:
                                test.interpretation = cor.corrected_interpretation
                            test.confidence = 1.0
                            test.is_valid_physiological = True
                            test.reviewer_comments = cor.reviewer_notes
                        elif act == "REJECT":
                            test.review_status = FieldReviewStatus.REVIEWER_REJECTED
                            test.reviewer_comments = cor.reviewer_notes

            elif cat == "MEDICATION":
                for med in bundle.medications:
                    if med.id == cor.target_id:
                        if act == "APPROVE":
                            med.review_status = FieldReviewStatus.REVIEWER_APPROVED
                            med.confidence = 1.0
                        elif act == "CORRECT":
                            med.review_status = FieldReviewStatus.REVIEWER_CORRECTED
                            if cor.corrected_value:
                                med.drug_name = str(cor.corrected_value)
                            if cor.corrected_unit:
                                med.dose_unit = cor.corrected_unit
                            med.confidence = 1.0
                        elif act == "REJECT":
                            med.review_status = FieldReviewStatus.REVIEWER_REJECTED

            elif cat == "DIAGNOSIS":
                for diag in bundle.diagnoses:
                    if diag.id == cor.target_id:
                        if act == "APPROVE":
                            diag.review_status = FieldReviewStatus.REVIEWER_APPROVED
                            diag.confidence = 1.0
                        elif act == "CORRECT":
                            diag.review_status = FieldReviewStatus.REVIEWER_CORRECTED
                            if cor.corrected_value:
                                diag.diagnosis_name = str(cor.corrected_value)
                            diag.confidence = 1.0
                        elif act == "REJECT":
                            diag.review_status = FieldReviewStatus.REVIEWER_REJECTED

        # Transition job status
        job.status = IngestionStatus.REVIEW_COMPLETED
        job.requires_human_review = False
        job.human_review_reasons.append(f"Human review completed by {reviewer_name} ({reviewer_id})")

        # Now commit verified items using the committer stage
        doc_file = store.document_files.get(job.document_id)
        if doc_file:
            metadata = DocumentMetadata(
                document_id=job.document_id,
                citizen_id=job.citizen_id,
                title=doc_file["filename"],
                filename=doc_file["filename"],
                content_type=doc_file["content_type"],
                size_bytes=doc_file["size_bytes"],
                sha256_hash=doc_file["sha256_hash"],
                storage_path=doc_file["storage_path"],
                uploaded_by=reviewer_id,
            )
            ctx = PipelineContext(
                metadata=metadata,
                raw_content=doc_file["raw_bytes"],
                job=job,
            )
            await self.committer_stage.process(ctx)

        # Dequeue from review_queue
        store.review_queue.pop(job_id, None)
        self._save_job(job)

        return job

    def get_job(self, job_id: str) -> Optional[DocumentIngestionJob]:
        return store.ingestion_jobs.get(job_id)

    def list_review_queue(self, citizen_id: Optional[str] = None) -> List[DocumentIngestionJob]:
        items = list(store.review_queue.values())
        if citizen_id:
            items = [j for j in items if j.citizen_id == citizen_id]
        return items

    def get_document_file(self, doc_id: str) -> Optional[Dict[str, Any]]:
        return store.document_files.get(doc_id)

    def _save_job(self, job: DocumentIngestionJob) -> None:
        job.updated_at = datetime.now(timezone.utc)
        store.ingestion_jobs[job.job_id] = job


ingestion_engine = ClinicalDocumentIngestionEngine()
