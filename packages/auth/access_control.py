from typing import Tuple, Optional
from fastapi import HTTPException, status
from packages.types.enums import UserRole, AuditAction
from packages.auth.jwt import TokenPayload
from packages.observability.audit import audit_logger
from services.store import store, CitizenRecord, UserRecord


class HealthcareAuthorizationEngine:
    """Enforces least privilege, tenant boundaries, care context, and consent-aware authorization.

    Standards:
    - DISHA / ABDM Healthcare Data Privacy Directives
    - Least Privilege & Need-to-Know Clinical Boundaries
    """

    @classmethod
    def can_access_citizen_record(
        cls,
        actor: TokenPayload,
        citizen: CitizenRecord,
        purpose: str = "CARE_DELIVERY"
    ) -> Tuple[bool, str]:
        # 1. Organization / Tenant Boundary Check
        # Primary tenant isolation: Users can only access records in their tenant (or state parent)
        if actor.tenant_id != citizen.tenant_id and actor.tenant_id != "karnataka_state_health":
            return False, f"Cross-tenant access prohibited. Actor tenant '{actor.tenant_id}' does not match citizen tenant '{citizen.tenant_id}'."

        # 2. Citizen Role (Self-Access Only)
        if actor.role == UserRole.CITIZEN:
            if citizen.user_id != actor.sub and citizen.id != actor.sub:
                return False, "Access denied. Citizens can only access their own medical records."
            return True, "Authorized: Citizen self-access"

        # 3. Purpose Mismatch Defense: Secondary Research/Analytics (TC-CON-003)
        # Clinical care context does NOT authorize secondary research without explicit research consent directive
        if purpose in ("RESEARCH", "RESEARCH_ANALYTICS", "SECONDARY_USE", "COMMERCIAL_USE"):
            grantee_ids = [actor.sub]
            user = store.get_user(actor.sub)
            if user:
                grantee_ids.extend([user.id, user.email])
            has_research_consent = any(store.has_active_consent(citizen.id, gid, purpose=purpose) for gid in set(grantee_ids))
            if has_research_consent:
                return True, f"Authorized: Explicit research consent directive granted for purpose '{purpose}'"
            return False, f"Access denied: Consent purpose mismatch. Requested purpose '{purpose}' is outside granted clinical scope without explicit research consent."

        # 4. Public Health Administrator (Aggregated / De-identified Only)
        if actor.role == UserRole.PUBLIC_HEALTH_ADMIN:
            return False, "Access denied. Public Health Administrators are restricted to aggregated population intelligence and cannot view identifiable patient health records."

        # 4. Clinician Care Context & Consent
        if actor.role == UserRole.CLINICIAN:
            clinician_user: Optional[UserRecord] = store.get_user(actor.sub)

            # Check if patient is in clinician's assigned panel
            is_assigned = clinician_user and (citizen.id in clinician_user.assigned_patients)

            # Check if citizen has granted active consent directive to clinician
            grantee_ids = [actor.sub]
            if clinician_user:
                grantee_ids.extend([clinician_user.id, clinician_user.email])
            has_consent = any(store.has_active_consent(citizen.id, gid, purpose=purpose) for gid in set(grantee_ids))

            # Check if there is an active/pending triage case for this citizen assigned to this clinician or unassigned
            triage_cases = store.list_triage_cases()
            has_pending_triage = any(
                tc.citizen_id == citizen.id and (not tc.assigned_clinician_id or tc.assigned_clinician_id == actor.sub or (clinician_user and tc.assigned_clinician_id == clinician_user.id))
                for tc in triage_cases
            )

            if is_assigned or has_consent or has_pending_triage:
                return True, "Authorized: Clinician assigned to care context, triage case, or active patient consent"
            return False, "Access denied. Clinician is not assigned to this patient's care context and no active consent directive exists."

        # 5. Health Worker Jurisdiction & Program Scope (TC-IAM-004)
        if actor.role == UserRole.HEALTH_WORKER:
            hw_user: Optional[UserRecord] = store.get_user(actor.sub)

            hw_programs = hw_user.assigned_programs if hw_user and hw_user.assigned_programs else []
            hw_jurisdiction = hw_user.assigned_jurisdiction if hw_user else "All"

            # Check active consent first (patient consent can override program/ward boundaries)
            grantee_ids = [actor.sub]
            if hw_user:
                grantee_ids.extend([hw_user.id, hw_user.email])
            has_consent = any(store.has_active_consent(citizen.id, gid, purpose=purpose) for gid in set(grantee_ids))
            if has_consent:
                return True, "Authorized: Active patient consent granted to Health Worker"

            # Check program-level isolation
            if hw_programs:
                if citizen.program_id and citizen.program_id not in hw_programs:
                    return False, f"Access denied. Worker assigned only to {hw_programs}; citizen belongs exclusively to program '{citizen.program_id}'."

                # Verify if citizen's village/ward belongs to one of the worker's assigned programs
                assigned_program_communities = set()
                for prog_id in hw_programs:
                    prog = store.get_program(prog_id)
                    if prog:
                        comms = getattr(prog, "communities", []) or (prog.get("communities", []) if isinstance(prog, dict) else [])
                        assigned_program_communities.update(comms)
                    else:
                        from services.health_worker.service import REGISTERED_PROGRAMS
                        for rp in REGISTERED_PROGRAMS:
                            if rp.id == prog_id:
                                assigned_program_communities.update(rp.communities)

                if assigned_program_communities and citizen.village_or_ward not in assigned_program_communities:
                    return False, f"Access denied. Citizen location '{citizen.village_or_ward}' does not belong to Health Worker assigned programs."

            # Check ward/village / district matching
            in_jurisdiction = (
                hw_jurisdiction == "All" or
                hw_jurisdiction in citizen.village_or_ward or
                hw_jurisdiction in citizen.district
            )

            if in_jurisdiction:
                return True, "Authorized: Health Worker in authorized program jurisdiction"
            return False, f"Access denied. Citizen location '{citizen.village_or_ward}' is outside Health Worker assigned jurisdiction '{hw_jurisdiction}'."

        # 6. System Administrator Least Privilege (TC-IAM-006)
        if actor.role == UserRole.SYSTEM_ADMIN:
            # Operational administration does not automatically grant unrestricted clinical-record access
            grantee_ids = [actor.sub]
            admin_user: Optional[UserRecord] = store.get_user(actor.sub)
            if admin_user:
                grantee_ids.extend([admin_user.id, admin_user.email])

            has_approved_auth = any(
                store.has_active_consent(citizen.id, gid, purpose=purpose) or
                store.has_active_consent(citizen.id, gid, purpose="ADMIN_AUDIT") or
                store.has_active_consent(citizen.id, gid, purpose="EMERGENCY_BREAKGLASS")
                for gid in set(grantee_ids)
            )
            if has_approved_auth:
                return True, "Authorized: System Administrator with approved authorization directive"
            return False, "Access denied. Operational administration does not automatically grant unrestricted clinical-record access without an approved authorization."

        return False, "Access denied: Role not permitted"

    @classmethod
    def enforce_citizen_access(
        cls,
        actor: TokenPayload,
        citizen: CitizenRecord,
        purpose: str = "CARE_DELIVERY"
    ):
        allowed, reason = cls.can_access_citizen_record(actor, citizen, purpose=purpose)
        if not allowed:
            audit_logger.record(
                tenant_id=citizen.tenant_id if citizen else actor.tenant_id,
                actor_id=actor.sub,
                actor_role=actor.role,
                action=AuditAction.ACCESS_DENIED,
                resource_type="Citizen",
                resource_id=citizen.id if citizen else "unknown",
                details={"reason": reason, "attempted_purpose": purpose},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=reason,
            )
