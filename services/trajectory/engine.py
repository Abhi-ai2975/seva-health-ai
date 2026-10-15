import uuid
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timezone
import math

from services.trajectory.models import (
    TrajectoryTrendState,
    TrajectoryDomain,
    RiskSnapshot,
    DomainTrajectoryComparison,
    CitizenTrajectoryReport,
)
from packages.clinical_models.triage import ClinicalTriageCase, SOAPReport
from packages.types.enums import TriageUrgency, ClinicianReviewStatus
from services.store import store


class RiskTrajectoryEngine:
    """Longitudinal Risk Trajectory Engine for SevaHealth AI.
    
    Maintains, compares, and explains multi-domain time-series risk trajectories.
    Enforces non-causal language and clinical safety standards.
    """

    MANDATORY_DOMAINS = [
        TrajectoryDomain.METABOLIC.value,
        TrajectoryDomain.DIABETES.value,
        TrajectoryDomain.HYPERTENSION.value,
        TrajectoryDomain.CARDIOVASCULAR.value,
        TrajectoryDomain.OBESITY.value,
        TrajectoryDomain.RENAL.value,
        TrajectoryDomain.LIFESTYLE.value,
    ]

    # Clinical sensitivity threshold for score delta significance
    SCORE_DELTA_THRESHOLD = 0.04  # 4 percentage points

    def check_snapshot_compatibility(
        self, snap_a: RiskSnapshot, snap_b: RiskSnapshot
    ) -> Tuple[bool, Optional[str]]:
        """Verifies mathematical and clinical comparability between two snapshots (TC-TRJ-003)."""
        # 1. Scoring scale check
        if snap_a.scoring_scale != snap_b.scoring_scale:
            return (
                False,
                f"Incompatible scoring scales detected: baseline uses '{snap_a.scoring_scale}' "
                f"while current uses '{snap_b.scoring_scale}'. Direct numerical comparison is refused."
            )

        # 2. Demonstration vs Clinically Validated check
        if snap_a.is_demonstration_only != snap_b.is_demonstration_only:
            return (
                False,
                "Cannot compare unvalidated demonstration proxy model with clinically validated production model. "
                "Direct numerical comparison is refused to prevent clinical misinterpretation."
            )

        # 3. Model Architecture check
        if snap_a.model_name != snap_b.model_name:
            return (
                False,
                f"Incompatible model architectures: baseline evaluated with '{snap_a.model_name}' "
                f"while current evaluated with '{snap_b.model_name}'. Direct numerical comparison is refused."
            )

        # 4. Major Version Divergence check
        v_a = snap_a.model_version.lstrip("v").split(".")[0] if snap_a.model_version else "1"
        v_b = snap_b.model_version.lstrip("v").split(".")[0] if snap_b.model_version else "1"
        v_a_digits = "".join(ch for ch in v_a if ch.isdigit()) or "1"
        v_b_digits = "".join(ch for ch in v_b if ch.isdigit()) or "1"
        if v_a_digits != v_b_digits:
            return (
                False,
                f"Incompatible model versions: baseline version '{snap_a.model_version}' vs "
                f"current version '{snap_b.model_version}' has major version divergence. "
                f"Direct numerical comparison is refused."
            )

        return (True, None)

    def evaluate_trajectory(
        self,
        citizen_id: str,
        historical_snapshots: List[RiskSnapshot],
    ) -> CitizenTrajectoryReport:
        """Evaluates longitudinal trajectories across the 7 mandatory domains
        by comparing Current vs Previous vs Baseline.
        """
        # Ensure chronological ordering
        sorted_snaps = sorted(historical_snapshots, key=lambda s: s.timestamp)
        n = len(sorted_snaps)

        if n == 0:
            return self._build_empty_report(citizen_id)

        baseline_snap = sorted_snaps[0]
        prev_snap = sorted_snaps[-2] if n >= 2 else None
        curr_snap = sorted_snaps[-1]

        # TC-TRJ-003: Check comparability if multi-point trajectory
        if n >= 2:
            is_comp, limitation_reason = self.check_snapshot_compatibility(baseline_snap, curr_snap)
            if not is_comp:
                # Build refused comparison report
                incomp_domains = {
                    d: DomainTrajectoryComparison(
                        domain=d,
                        trend=TrajectoryTrendState.INSUFFICIENT_DATA,
                        is_comparable=False,
                        comparison_note=limitation_reason,
                        contributing_factors=["Direct numerical comparison refused due to incompatible models or scoring scales."],
                        clinical_note=limitation_reason or "Incompatible scoring models.",
                    )
                    for d in self.MANDATORY_DOMAINS
                }
                return CitizenTrajectoryReport(
                    citizen_id=citizen_id,
                    calculated_at=datetime.now(timezone.utc),
                    total_snapshots=n,
                    baseline_snapshot=baseline_snap,
                    previous_snapshot=prev_snap,
                    current_snapshot=curr_snap,
                    historical_snapshots=sorted_snaps,
                    domain_trajectories=incomp_domains,
                    overall_trend=TrajectoryTrendState.INSUFFICIENT_DATA,
                    composite_current_score=None,
                    composite_baseline_score=None,
                    composite_change_percentage=None,
                    clinical_summary=f"Direct numerical comparison is refused: {limitation_reason}",
                    is_comparable=False,
                    numerical_comparison_refused=True,
                    comparison_limitations=[limitation_reason] if limitation_reason else [],
                    review_required=True,
                    review_workflow_status="CLINICIAN_REVIEW_REQUIRED",
                    review_priority="ROUTINE",
                    recommended_review_actions=[
                        "Re-baseline citizen under unified scoring model",
                        "Perform manual clinical review of raw observations without automated delta",
                    ],
                )

        domain_comparisons: Dict[str, DomainTrajectoryComparison] = {}
        domain_trends: List[TrajectoryTrendState] = []
        comp_curr_sum = 0.0
        comp_base_sum = 0.0
        valid_domains_count = 0

        for domain in self.MANDATORY_DOMAINS:
            comparison = self._evaluate_domain(
                domain=domain,
                baseline_snap=baseline_snap,
                prev_snap=prev_snap,
                curr_snap=curr_snap,
                total_snapshots=n,
            )
            domain_comparisons[domain] = comparison
            domain_trends.append(comparison.trend)

            if comparison.current_score is not None:
                comp_curr_sum += comparison.current_score
                valid_domains_count += 1
            if comparison.baseline_score is not None:
                comp_base_sum += comparison.baseline_score

        # Determine composite scores and deltas
        if valid_domains_count > 0:
            composite_current = round(comp_curr_sum / valid_domains_count, 3)
            composite_baseline = round(comp_base_sum / valid_domains_count, 3)
            if composite_baseline > 0:
                comp_change_pct = round(((composite_current - composite_baseline) / composite_baseline) * 100, 1)
            else:
                comp_change_pct = 0.0
        else:
            composite_current = None
            composite_baseline = None
            comp_change_pct = None

        # Determine overall trend
        overall_trend = self._determine_overall_trend(domain_trends, composite_current, composite_baseline)

        # Build clinical summary
        clinical_summary = self._generate_clinical_summary(
            overall_trend=overall_trend,
            comp_change_pct=comp_change_pct,
            domain_comparisons=domain_comparisons,
            total_snapshots=n,
        )

        # TC-TRJ-002: Clinical review workflow and automated triage case escalation
        review_required = False
        review_status = "STANDARD"
        review_priority = "ROUTINE"
        triage_case_id: Optional[str] = None
        recommended_actions: List[str] = []

        if overall_trend == TrajectoryTrendState.WORSENING:
            review_required = True
            review_status = "CLINICIAN_REVIEW_REQUIRED"
            is_critical = any(c.current_tier == "CRITICAL" for c in domain_comparisons.values())
            review_priority = "EMERGENT" if is_critical else "PRIORITY"
            recommended_actions = [
                "Physician clinical review required within 24-48 hours",
                "Review biomarker elevations and verify adherence to therapy",
                "Update personalized preventive care plan and order lab confirmation",
            ]

            # Register automated ClinicalTriageCase into store
            citizen = store.get_citizen(citizen_id)
            tenant_id = citizen.tenant_id if citizen else "default-tenant"
            c_name = f"{citizen.first_name} {citizen.last_name}" if citizen else "Citizen"
            triage_case_id = str(uuid.uuid4())

            urgency_enum = TriageUrgency.EMERGENT if review_priority == "EMERGENT" else TriageUrgency.PRIORITY
            worsening_names = [d for d, c in domain_comparisons.items() if c.trend == TrajectoryTrendState.WORSENING]

            soap_note = SOAPReport(
                subjective=f"Patient {c_name} presents with deteriorating longitudinal risk trajectory across key cardiometabolic domains.",
                objective=f"Composite Risk: {composite_current*100 if composite_current else 0:.0f}%, Delta from baseline: {comp_change_pct:+.1f}%. Worsening domains: {', '.join(worsening_names)}.",
                assessment="Algorithmic longitudinal risk trajectory indicates worsening disease progression. Requires prompt clinician review and intervention adjustment.",
                plan="1. Review baseline and current vitals. 2. Verify adherence to prescribed lifestyle/pharmacotherapy. 3. Schedule tele-consultation.",
            )
            triage_case = ClinicalTriageCase(
                id=triage_case_id,
                tenant_id=tenant_id,
                citizen_id=citizen_id,
                citizen_name=c_name,
                risk_assessment_id=f"traj-{citizen_id}",
                urgency=urgency_enum,
                escalation_reason=f"Worsening multi-domain trajectory: {comp_change_pct:+.1f}% composite increase across {len(worsening_names)} domains",
                soap_note=soap_note,
                status=ClinicianReviewStatus.PENDING,
            )
            store.add_triage_case(triage_case)

        elif overall_trend == TrajectoryTrendState.IMPROVING:
            review_required = False
            review_status = "STANDARD"
            review_priority = "ROUTINE"
            recommended_actions = [
                "Maintain ongoing preventive interventions and reinforce behavioral compliance",
                "Continue standard quarterly monitoring intervals",
            ]
        else:
            review_required = False
            review_status = "STANDARD"
            review_priority = "ROUTINE"
            recommended_actions = ["Continue regular screening schedule"]

        return CitizenTrajectoryReport(
            citizen_id=citizen_id,
            calculated_at=datetime.now(timezone.utc),
            total_snapshots=n,
            baseline_snapshot=baseline_snap,
            previous_snapshot=prev_snap,
            current_snapshot=curr_snap,
            historical_snapshots=sorted_snaps,
            domain_trajectories=domain_comparisons,
            overall_trend=overall_trend,
            composite_current_score=composite_current,
            composite_baseline_score=composite_baseline,
            composite_change_percentage=comp_change_pct,
            clinical_summary=clinical_summary,
            is_comparable=True,
            numerical_comparison_refused=False,
            comparison_limitations=[],
            review_required=review_required,
            review_workflow_status=review_status,
            review_priority=review_priority,
            triage_case_id=triage_case_id,
            recommended_review_actions=recommended_actions,
        )

    def _evaluate_domain(
        self,
        domain: str,
        baseline_snap: RiskSnapshot,
        prev_snap: Optional[RiskSnapshot],
        curr_snap: RiskSnapshot,
        total_snapshots: int,
    ) -> DomainTrajectoryComparison:
        """Evaluates three-way trajectory (Current vs Previous vs Baseline) for a single domain."""
        curr_score = curr_snap.domain_scores.get(domain)
        curr_tier = curr_snap.domain_tiers.get(domain, "INSUFFICIENT_DATA")

        prev_score = prev_snap.domain_scores.get(domain) if prev_snap else None
        prev_tier = prev_snap.domain_tiers.get(domain) if prev_snap else None

        base_score = baseline_snap.domain_scores.get(domain)
        base_tier = baseline_snap.domain_tiers.get(domain)

        # 1. Check for Insufficient Data
        if curr_score is None:
            return DomainTrajectoryComparison(
                domain=domain,
                current_score=None,
                current_tier="INSUFFICIENT_DATA",
                previous_score=prev_score,
                previous_tier=prev_tier,
                baseline_score=base_score,
                baseline_tier=base_tier,
                trend=TrajectoryTrendState.INSUFFICIENT_DATA,
                confidence=0.0,
                data_completeness=0.0,
                contributing_factors=["Insufficient domain-specific measurements available for longitudinal comparison."],
                clinical_note="Baseline or recent biomarker assessments required to establish trajectory.",
            )

        # 2. Compute Mathematical Deltas
        abs_delta_prev = None
        if prev_score is not None:
            abs_delta_prev = round(curr_score - prev_score, 3)

        abs_delta_base = None
        change_pct_base = None
        if base_score is not None:
            abs_delta_base = round(curr_score - base_score, 3)
            if base_score > 0.001:
                change_pct_base = round(((curr_score - base_score) / base_score) * 100, 1)
            else:
                change_pct_base = 0.0

        # 3. Detect Trend (IMPROVING / STABLE / WORSENING / INSUFFICIENT_DATA)
        trend = self._detect_domain_trend(
            curr_score=curr_score,
            curr_tier=curr_tier,
            prev_score=prev_score,
            prev_tier=prev_tier,
            base_score=base_score,
            total_snapshots=total_snapshots,
        )

        # 4. Formulate Explainable Contributing Factors (Non-Causal Phrasing)
        factors = self._formulate_contributing_factors(
            domain=domain,
            curr_snap=curr_snap,
            prev_snap=prev_snap or baseline_snap,
            trend=trend,
        )

        # Data completeness
        completeness = curr_snap.data_completeness if curr_snap.data_completeness > 0 else 0.85
        conf = round(min(1.0, curr_snap.confidence * (1.0 if total_snapshots >= 2 else 0.75)), 2)

        return DomainTrajectoryComparison(
            domain=domain,
            current_score=curr_score,
            current_tier=curr_tier,
            previous_score=prev_score,
            previous_tier=prev_tier,
            baseline_score=base_score,
            baseline_tier=base_tier,
            trend=trend,
            change_percentage_from_baseline=change_pct_base,
            absolute_delta_from_previous=abs_delta_prev,
            absolute_delta_from_baseline=abs_delta_base,
            contributing_factors=factors,
            confidence=conf,
            data_completeness=completeness,
            clinical_note=self._domain_clinical_note(domain, trend, change_pct_base),
        )

    def _detect_domain_trend(
        self,
        curr_score: float,
        curr_tier: str,
        prev_score: Optional[float],
        prev_tier: Optional[str],
        base_score: Optional[float],
        total_snapshots: int,
    ) -> TrajectoryTrendState:
        """Determines whether trajectory is IMPROVING, STABLE, or WORSENING."""
        if total_snapshots < 2:
            return TrajectoryTrendState.STABLE

        # Compare primarily against previous snapshot; if identical, compare against baseline
        compare_score = prev_score if prev_score is not None else base_score
        if compare_score is None:
            return TrajectoryTrendState.STABLE

        delta = curr_score - compare_score

        if delta <= -self.SCORE_DELTA_THRESHOLD:
            return TrajectoryTrendState.IMPROVING
        elif delta >= self.SCORE_DELTA_THRESHOLD:
            return TrajectoryTrendState.WORSENING
        else:
            # Check tier migration
            tier_rank = {"LOW": 1, "MODERATE": 2, "HIGH": 3, "CRITICAL": 4}
            c_rank = tier_rank.get(curr_tier, 0)
            p_rank = tier_rank.get(prev_tier or "", 0)
            if c_rank > 0 and p_rank > 0:
                if c_rank > p_rank:
                    return TrajectoryTrendState.WORSENING
                elif c_rank < p_rank:
                    return TrajectoryTrendState.IMPROVING

            return TrajectoryTrendState.STABLE

    def _formulate_contributing_factors(
        self,
        domain: str,
        curr_snap: RiskSnapshot,
        prev_snap: RiskSnapshot,
        trend: TrajectoryTrendState,
    ) -> List[str]:
        """Constructs clinical driver statements strictly adhering to non-causal language.
        
        ALLOWED PHRASING:
        - "associated with"
        - "contributing factor"
        - "may be contributing"
        - "correlated with"
        
        FORBIDDEN PHRASING:
        - "caused by"
        """
        factors: List[str] = []
        c_bio = curr_snap.key_biomarkers
        p_bio = prev_snap.key_biomarkers

        if domain == TrajectoryDomain.DIABETES.value:
            # Check HbA1c
            if "HBA1C" in c_bio and "HBA1C" in p_bio:
                d_hba1c = round(float(c_bio["HBA1C"]) - float(p_bio["HBA1C"]), 2)
                if d_hba1c > 0.2:
                    factors.append(f"Elevated HbA1c ({c_bio['HBA1C']}%, +{d_hba1c}%) is associated with higher glycemic strain.")
                elif d_hba1c < -0.2:
                    factors.append(f"HbA1c reduction ({c_bio['HBA1C']}%, {d_hba1c}%) is associated with improved glycemic regulation.")
            # Check Fasting Glucose
            if "FASTING_GLUCOSE" in c_bio and "FASTING_GLUCOSE" in p_bio:
                d_fbg = round(float(c_bio["FASTING_GLUCOSE"]) - float(p_bio["FASTING_GLUCOSE"]), 1)
                if d_fbg >= 10.0:
                    factors.append(f"Fasting blood glucose increase (+{d_fbg} mg/dL) may be contributing to prediabetic progression.")
                elif d_fbg <= -10.0:
                    factors.append(f"Fasting blood glucose reduction ({d_fbg} mg/dL) is a contributing factor to metabolic stabilization.")

        elif domain == TrajectoryDomain.HYPERTENSION.value:
            # Check SBP / DBP
            if "SYSTOLIC_BP" in c_bio and "SYSTOLIC_BP" in p_bio:
                d_sbp = round(float(c_bio["SYSTOLIC_BP"]) - float(p_bio["SYSTOLIC_BP"]), 1)
                if d_sbp >= 8.0:
                    factors.append(f"Systolic blood pressure elevation (+{d_sbp} mmHg) is associated with increased arterial vascular strain.")
                elif d_sbp <= -8.0:
                    factors.append(f"Systolic blood pressure reduction ({d_sbp} mmHg) is a contributing factor to vascular decompression.")
            if "DIASTOLIC_BP" in c_bio and "DIASTOLIC_BP" in p_bio:
                d_dbp = round(float(c_bio["DIASTOLIC_BP"]) - float(p_bio["DIASTOLIC_BP"]), 1)
                if d_dbp >= 6.0:
                    factors.append(f"Diastolic pressure rise (+{d_dbp} mmHg) may be contributing to systemic vascular resistance.")

        elif domain == TrajectoryDomain.OBESITY.value or domain == TrajectoryDomain.METABOLIC.value:
            # Check Waist Circumference
            if "WAIST_CIRCUMFERENCE" in c_bio and "WAIST_CIRCUMFERENCE" in p_bio:
                d_w = round(float(c_bio["WAIST_CIRCUMFERENCE"]) - float(p_bio["WAIST_CIRCUMFERENCE"]), 1)
                if d_w >= 2.0:
                    factors.append(f"Increase in waist circumference (+{d_w} cm) is a contributing factor to central visceral adiposity.")
                elif d_w <= -2.0:
                    factors.append(f"Reduction in abdominal circumference ({d_w} cm) is associated with decreased metabolic risk.")
            # Check BMI / Weight
            if "BMI" in c_bio and "BMI" in p_bio:
                d_bmi = round(float(c_bio["BMI"]) - float(p_bio["BMI"]), 2)
                if d_bmi >= 0.5:
                    factors.append(f"Body mass index increase (+{d_bmi} kg/m²) may be contributing to insulin resistance.")
                elif d_bmi <= -0.5:
                    factors.append(f"BMI reduction ({d_bmi} kg/m²) is associated with improved anthropometric profile.")
            # Check Triglycerides
            if "TRIGLYCERIDES" in c_bio and "TRIGLYCERIDES" in p_bio:
                d_tg = round(float(c_bio["TRIGLYCERIDES"]) - float(p_bio["TRIGLYCERIDES"]), 1)
                if d_tg >= 20.0:
                    factors.append(f"Serum triglyceride elevation (+{d_tg} mg/dL) is associated with atherogenic dyslipidemia.")
                elif d_tg <= -20.0:
                    factors.append(f"Triglyceride improvement ({d_tg} mg/dL) is a contributing factor to metabolic health.")

        elif domain == TrajectoryDomain.CARDIOVASCULAR.value:
            if "TOTAL_CHOLESTEROL" in c_bio and "TOTAL_CHOLESTEROL" in p_bio:
                d_tc = round(float(c_bio["TOTAL_CHOLESTEROL"]) - float(p_bio["TOTAL_CHOLESTEROL"]), 1)
                if d_tc >= 15.0:
                    factors.append(f"Total cholesterol rise (+{d_tc} mg/dL) may be contributing to 10-year ASCVD probability.")
                elif d_tc <= -15.0:
                    factors.append(f"Total cholesterol reduction ({d_tc} mg/dL) is associated with reduced cardiovascular risk.")
            if c_bio.get("SMOKING") and not p_bio.get("SMOKING"):
                factors.append("Active tobacco consumption is a major contributing factor to endothelial dysfunction.")
            elif not c_bio.get("SMOKING") and p_bio.get("SMOKING"):
                factors.append("Tobacco cessation is associated with substantial cardiovascular risk reduction.")

        elif domain == TrajectoryDomain.RENAL.value:
            if "EGFR" in c_bio and "EGFR" in p_bio:
                d_egfr = round(float(c_bio["EGFR"]) - float(p_bio["EGFR"]), 1)
                if d_egfr <= -5.0:
                    factors.append(f"Glomerular filtration rate decline ({d_egfr} mL/min/1.73m²) is associated with reduced renal reserve.")
                elif d_egfr >= 5.0:
                    factors.append(f"Preserved or improved eGFR (+{d_egfr} mL/min/1.73m²) is a contributing factor to renal stability.")
            elif "SERUM_CREATININE" in c_bio and "SERUM_CREATININE" in p_bio:
                d_scr = round(float(c_bio["SERUM_CREATININE"]) - float(p_bio["SERUM_CREATININE"]), 2)
                if d_scr >= 0.2:
                    factors.append(f"Serum creatinine rise (+{d_scr} mg/dL) may be contributing to nephron filtration strain.")

        elif domain == TrajectoryDomain.LIFESTYLE.value:
            # Check Steps
            if "STEPS" in c_bio and "STEPS" in p_bio:
                d_steps = int(c_bio["STEPS"]) - int(p_bio["STEPS"])
                if d_steps <= -1500:
                    factors.append(f"Decline in daily physical activity ({d_steps} steps/day) is a contributing factor to deconditioning.")
                elif d_steps >= 1500:
                    factors.append(f"Increased daily walking (+{d_steps} steps/day) is associated with favorable metabolic expenditure.")
            # Check HRV
            if "HRV" in c_bio and "HRV" in p_bio:
                d_hrv = round(float(c_bio["HRV"]) - float(p_bio["HRV"]), 1)
                if d_hrv <= -8.0:
                    factors.append(f"HRV suppression ({d_hrv} ms) is associated with chronic autonomic sympathetic strain.")
                elif d_hrv >= 8.0:
                    factors.append(f"HRV recovery (+{d_hrv} ms) is a contributing factor to parasympathetic tone recovery.")

        # Fallback if no specific biomarker delta was large enough
        if not factors:
            if trend == TrajectoryTrendState.WORSENING:
                factors.append(f"Subtle aggregate shift across biometric indicators may be contributing to rising {domain} risk.")
            elif trend == TrajectoryTrendState.IMPROVING:
                factors.append(f"Consistent favorable biomarker stabilization is associated with improving {domain} risk.")
            else:
                factors.append(f"Biometric and lifestyle parameters remain consistent with baseline observations.")

        # Double check: strictly ensure "caused by" is never in factors
        sanitized_factors = [f.replace("caused by", "associated with") for f in factors]
        return sanitized_factors

    def _domain_clinical_note(self, domain: str, trend: TrajectoryTrendState, change_pct: Optional[float]) -> str:
        pct_str = f" ({change_pct:+.1f}% from baseline)" if change_pct is not None else ""
        if trend == TrajectoryTrendState.WORSENING:
            return f"Negative longitudinal trajectory detected{pct_str}. Clinical follow-up recommended."
        elif trend == TrajectoryTrendState.IMPROVING:
            return f"Favorable risk trajectory observed{pct_str}. Reinforce ongoing preventive interventions."
        elif trend == TrajectoryTrendState.STABLE:
            return f"Risk parameters remain stable within reference margins{pct_str}."
        return "Insufficient longitudinal time points to establish trend."

    def _determine_overall_trend(
        self,
        domain_trends: List[TrajectoryTrendState],
        curr_comp: Optional[float],
        base_comp: Optional[float],
    ) -> TrajectoryTrendState:
        worsening_count = sum(1 for t in domain_trends if t == TrajectoryTrendState.WORSENING)
        improving_count = sum(1 for t in domain_trends if t == TrajectoryTrendState.IMPROVING)

        if curr_comp is not None and base_comp is not None:
            comp_delta = curr_comp - base_comp
            if comp_delta >= self.SCORE_DELTA_THRESHOLD or worsening_count >= 2:
                return TrajectoryTrendState.WORSENING
            elif comp_delta <= -self.SCORE_DELTA_THRESHOLD or (improving_count >= 2 and worsening_count == 0):
                return TrajectoryTrendState.IMPROVING

        if worsening_count > improving_count:
            return TrajectoryTrendState.WORSENING
        elif improving_count > worsening_count:
            return TrajectoryTrendState.IMPROVING
        elif any(t == TrajectoryTrendState.STABLE for t in domain_trends):
            return TrajectoryTrendState.STABLE
        return TrajectoryTrendState.INSUFFICIENT_DATA

    def _generate_clinical_summary(
        self,
        overall_trend: TrajectoryTrendState,
        comp_change_pct: Optional[float],
        domain_comparisons: Dict[str, DomainTrajectoryComparison],
        total_snapshots: int,
    ) -> str:
        if total_snapshots <= 1:
            return "Baseline assessment recorded. Minimum 2 screening cycles required to compute directional trajectory."

        worsening_doms = [d for d, c in domain_comparisons.items() if c.trend == TrajectoryTrendState.WORSENING]
        improving_doms = [d for d, c in domain_comparisons.items() if c.trend == TrajectoryTrendState.IMPROVING]

        pct_info = f" ({comp_change_pct:+.1f}% multi-domain delta)" if comp_change_pct is not None else ""

        if overall_trend == TrajectoryTrendState.WORSENING:
            w_str = ", ".join(worsening_doms) if worsening_doms else "overall metabolic parameters"
            return (
                f"Longitudinal trajectory indicates WORSENING risk profile{pct_info}. "
                f"Negative trend observed predominantly in: {w_str}. "
                f"Clinician review and intervention adjustment recommended."
            )
        elif overall_trend == TrajectoryTrendState.IMPROVING:
            i_str = ", ".join(improving_doms) if improving_doms else "overall lifestyle markers"
            return (
                f"Longitudinal trajectory demonstrates IMPROVING risk profile{pct_info}. "
                f"Favorable trend observed across: {i_str}. Continue active care plan adherence."
            )
        else:
            return f"Longitudinal trajectory reflects STABLE disease risk across all evaluated domains{pct_info}."

    def _build_empty_report(self, citizen_id: str) -> CitizenTrajectoryReport:
        empty_doms = {
            d: DomainTrajectoryComparison(
                domain=d,
                trend=TrajectoryTrendState.INSUFFICIENT_DATA,
                contributing_factors=["No historical screening data available."],
            )
            for d in self.MANDATORY_DOMAINS
        }
        return CitizenTrajectoryReport(
            citizen_id=citizen_id,
            calculated_at=datetime.now(timezone.utc),
            total_snapshots=0,
            domain_trajectories=empty_doms,
            overall_trend=TrajectoryTrendState.INSUFFICIENT_DATA,
            clinical_summary="No longitudinal observations recorded for this citizen.",
        )

    # =========================================================================
    # VISUAL TRAJECTORY GENERATION (HTML & SVG)
    # =========================================================================

    def generate_visual_html(self, report: CitizenTrajectoryReport, citizen_name: str = "Citizen") -> str:
        """Generates self-contained, theme-adaptive interactive visual trajectory
        dashboard adhering to generative_ui guidelines with Tailwind CSS.
        """
        trend_colors = {
            "IMPROVING": "text-emerald-600 bg-emerald-50 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-800",
            "STABLE": "text-blue-600 bg-blue-50 border-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-800",
            "WORSENING": "text-rose-600 bg-rose-50 border-rose-200 dark:bg-rose-950/40 dark:text-rose-300 dark:border-rose-800",
            "INSUFFICIENT_DATA": "text-slate-600 bg-slate-50 border-slate-200 dark:bg-slate-900 dark:text-slate-400 dark:border-slate-800",
        }

        badge_cls = trend_colors.get(report.overall_trend.value, trend_colors["STABLE"])

        # Build Domain Cards HTML
        domain_cards_html = ""
        for dom, comp in report.domain_trajectories.items():
            t_cls = trend_colors.get(comp.trend.value, trend_colors["STABLE"])
            curr_score_display = f"{int(comp.current_score * 100)}%" if comp.current_score is not None else "N/A"
            prev_score_display = f"{int(comp.previous_score * 100)}%" if comp.previous_score is not None else "—"
            base_score_display = f"{int(comp.baseline_score * 100)}%" if comp.baseline_score is not None else "—"
            
            delta_str = ""
            if comp.change_percentage_from_baseline is not None:
                sign = "+" if comp.change_percentage_from_baseline > 0 else ""
                delta_str = f"<span class='text-xs font-semibold px-2 py-0.5 rounded-full {t_cls}'>{sign}{comp.change_percentage_from_baseline:.1f}%</span>"

            factors_html = "".join([f"<li class='text-xs text-[var(--muted-foreground)] flex items-start gap-1.5'><span class='text-primary mt-0.5'>•</span><span>{f}</span></li>" for f in comp.contributing_factors[:2]])

            domain_cards_html += f"""
            <div class="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 shadow-sm hover:shadow transition-shadow">
                <div class="flex items-center justify-between mb-3">
                    <div class="flex items-center gap-2">
                        <span class="font-semibold text-sm capitalize text-[var(--foreground)]">{dom} Risk</span>
                        {delta_str}
                    </div>
                    <span class="text-xs px-2.5 py-1 rounded-full font-bold uppercase tracking-wider border {t_cls}">
                        {comp.trend.value}
                    </span>
                </div>
                
                <div class="grid grid-cols-3 gap-2 py-2 mb-3 bg-[var(--content)]/40 rounded-lg text-center border border-[var(--border)]/60">
                    <div>
                        <div class="text-[11px] text-[var(--muted-foreground)] uppercase">Baseline</div>
                        <div class="text-xs font-semibold text-[var(--foreground)]">{base_score_display}</div>
                        <div class="text-[10px] text-[var(--muted-foreground)] capitalize">{comp.baseline_tier or '—'}</div>
                    </div>
                    <div>
                        <div class="text-[11px] text-[var(--muted-foreground)] uppercase">Previous</div>
                        <div class="text-xs font-semibold text-[var(--foreground)]">{prev_score_display}</div>
                        <div class="text-[10px] text-[var(--muted-foreground)] capitalize">{comp.previous_tier or '—'}</div>
                    </div>
                    <div>
                        <div class="text-[11px] text-primary font-bold uppercase">Current</div>
                        <div class="text-xs font-extrabold text-[var(--foreground)]">{curr_score_display}</div>
                        <div class="text-[10px] font-medium text-primary capitalize">{comp.current_tier}</div>
                    </div>
                </div>

                <div class="space-y-1">
                    <div class="text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide">Key Associated Drivers:</div>
                    <ul class="space-y-1">
                        {factors_html}
                    </ul>
                </div>
            </div>
            """

        # SVG Longitudinal Curve
        svg_curve = self.generate_visual_svg(report)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SevaHealth AI: Longitudinal Risk Trajectory</title>
    <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
    <style>
        :root {{
            --background: #f8fafc;
            --content: #f1f5f9;
            --card: #ffffff;
            --border: #e2e8f0;
            --foreground: #0f172a;
            --muted-foreground: #64748b;
            --primary: #0284c7;
            --primary-foreground: #ffffff;
        }}
        @media (prefers-color-scheme: dark) {{
            :root {{
                --background: #090d16;
                --content: #131b2e;
                --card: #182238;
                --border: #263554;
                --foreground: #f8fafc;
                --muted-foreground: #94a3b8;
                --primary: #38bdf8;
                --primary-foreground: #090d16;
            }}
        }}
    </style>
</head>
<body class="bg-[var(--background)] text-[var(--foreground)] p-4 md:p-6 font-sans antialiased min-h-screen">
    <div class="max-w-5xl mx-auto space-y-6">
        
        <!-- Header Banner -->
        <div class="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-sm flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
            <div>
                <div class="flex items-center gap-2">
                    <span class="text-xs font-bold uppercase tracking-widest text-primary">SevaHealth AI</span>
                    <span class="text-xs text-[var(--muted-foreground)]">• Decision Intelligence</span>
                </div>
                <h1 class="text-xl md:text-2xl font-black text-[var(--foreground)] mt-0.5">
                    Longitudinal Risk Trajectory: {citizen_name}
                </h1>
                <p class="text-xs text-[var(--muted-foreground)] mt-1">
                    Multi-Domain Evolution: Current vs Previous vs Baseline ({report.total_snapshots} Screening Cycles Recorded)
                </p>
            </div>
            
            <div class="flex items-center gap-3">
                <div class="text-right">
                    <div class="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)]">Overall Trajectory</div>
                    <div class="text-sm font-extrabold text-[var(--foreground)]">{report.composite_current_score*100 if report.composite_current_score else 0:.0f}% Composite Risk</div>
                </div>
                <span class="text-xs px-3 py-1.5 rounded-full font-black uppercase tracking-wider border {badge_cls}">
                    {report.overall_trend.value}
                </span>
            </div>
        </div>

        <!-- Visual Timeline & Sparkline Chart -->
        <div class="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-sm">
            <div class="flex items-center justify-between mb-2">
                <h2 class="text-sm font-bold uppercase tracking-wide text-[var(--foreground)]">
                    Cross-Domain Risk Progression Timeline
                </h2>
                <span class="text-xs text-[var(--muted-foreground)]">Baseline → Intermediate → Latest</span>
            </div>
            <div class="w-full overflow-x-auto py-2">
                {svg_curve}
            </div>
        </div>

        <!-- 7 Domain Comparison Grid -->
        <div>
            <div class="flex items-center justify-between mb-3">
                <h2 class="text-sm font-bold uppercase tracking-wide text-[var(--foreground)]">
                    Domain-Specific Trajectories (7 Core Dimensions)
                </h2>
                <span class="text-xs text-[var(--muted-foreground)]">Strictly Non-Causal Feature Attribution</span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {domain_cards_html}
            </div>
        </div>

        <!-- Clinical Safety & Summary Disclaimer -->
        <div class="rounded-xl border border-amber-200/60 dark:border-amber-900/60 bg-amber-50/60 dark:bg-amber-950/20 p-4">
            <div class="flex items-start gap-3">
                <span class="text-amber-600 dark:text-amber-400 font-bold text-base mt-0.5">ℹ</span>
                <div class="space-y-1">
                    <div class="text-xs font-bold text-amber-900 dark:text-amber-200 uppercase tracking-wider">
                        Clinical Decision Support Summary
                    </div>
                    <p class="text-xs text-amber-800 dark:text-amber-300 leading-relaxed">
                        {report.clinical_summary}
                    </p>
                    <p class="text-[11px] text-amber-700 dark:text-amber-400 italic pt-1 border-t border-amber-200/40 dark:border-amber-900/40">
                        {report.clinical_safety_notice}
                    </p>
                </div>
            </div>
        </div>

    </div>
</body>
</html>"""
        return html

    def generate_visual_svg(self, report: CitizenTrajectoryReport) -> str:
        """Generates a high-contrast SVG vector trajectory chart."""
        snaps = report.historical_snapshots
        if not snaps:
            return "<svg width='600' height='160'><text x='50%' y='50%' text-anchor='middle' fill='#64748b' font-size='12'>No Historical Trajectory Data</text></svg>"

        width = 720
        height = 200
        padding_x = 70
        padding_y = 35

        plot_w = width - (padding_x * 2)
        plot_h = height - (padding_y * 2)

        # X coords for snapshots
        n = len(snaps)
        x_step = plot_w / max(1, n - 1) if n > 1 else plot_w / 2
        x_coords = [padding_x + (i * x_step if n > 1 else plot_w / 2) for i in range(n)]

        domain_colors = {
            "diabetes": "#ef4444",       # Red
            "hypertension": "#f97316",   # Orange
            "cardiovascular": "#a855f7", # Purple
            "metabolic": "#eab308",      # Amber
            "obesity": "#06b6d4",        # Cyan
            "renal": "#3b82f6",          # Blue
            "lifestyle": "#10b981",      # Emerald
        }

        # Build paths for domains
        lines_svg = []
        dots_svg = []

        for dom, col in domain_colors.items():
            pts = []
            for i, snap in enumerate(snaps):
                score = snap.domain_scores.get(dom)
                if score is not None:
                    y = padding_y + (1.0 - score) * plot_h
                    x = x_coords[i]
                    pts.append((x, y))
                    dots_svg.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3.5' fill='{col}' stroke='#ffffff' stroke-width='1.5'/>")

            if len(pts) >= 2:
                path_d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f} " + " ".join([f"L {p[0]:.1f} {p[1]:.1f}" for p in pts[1:]])
                lines_svg.append(f"<path d='{path_d}' fill='none' stroke='{col}' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'/>")
            elif len(pts) == 1:
                dots_svg.append(f"<circle cx='{pts[0][0]:.1f}' cy='{pts[0][1]:.1f}' r='4' fill='{col}'/>")

        # Background grid & labels
        grid_svg = []
        for level, label in [(0.25, "Low"), (0.55, "Mod"), (0.85, "High")]:
            y_level = padding_y + (1.0 - level) * plot_h
            grid_svg.append(f"<line x1='{padding_x}' y1='{y_level:.1f}' x2='{width-padding_x}' y2='{y_level:.1f}' stroke='#cbd5e1' stroke-dasharray='3 3' stroke-width='1'/>")
            grid_svg.append(f"<text x='{padding_x - 10}' y='{y_level + 4:.1f}' text-anchor='end' fill='#64748b' font-size='10' font-weight='500'>{label}</text>")

        # X-axis timeline markers
        for i, snap in enumerate(snaps):
            x = x_coords[i]
            dt_label = snap.timestamp.strftime("%b %d")
            grid_svg.append(f"<line x1='{x:.1f}' y1='{padding_y}' x2='{x:.1f}' y2='{height-padding_y}' stroke='#e2e8f0' stroke-width='1'/>")
            grid_svg.append(f"<text x='{x:.1f}' y='{height - padding_y + 16}' text-anchor='middle' fill='#64748b' font-size='11' font-weight='600'>{dt_label}</text>")

        # Legend
        legend_items = []
        for i, (dom, col) in enumerate(domain_colors.items()):
            lx = 50 + (i * 90)
            legend_items.append(f"<circle cx='{lx}' cy='15' r='4' fill='{col}'/><text x='{lx + 8}' y='18' fill='#475569' font-size='10' font-weight='500' text-transform='capitalize'>{dom}</text>")

        svg_content = f"""
        <svg viewBox="0 0 {width} {height}" class="w-full h-auto" xmlns="http://www.w3.org/2000/svg">
            <rect width="{width}" height="{height}" fill="transparent"/>
            <g id="legend">{''.join(legend_items)}</g>
            <g id="grid">{''.join(grid_svg)}</g>
            <g id="lines">{''.join(lines_svg)}</g>
            <g id="dots">{''.join(dots_svg)}</g>
        </svg>
        """
        return svg_content


# Global singleton instance
risk_trajectory_engine = RiskTrajectoryEngine()
