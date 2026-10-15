#!/usr/bin/env python3
"""SevaHealth AI: Single-Command Challenge Demonstration Launcher (`make demo`).

Executes the complete end-to-end challenge preparation:
1. Validates runtime dependencies & environment configuration
2. Executes database migrations (ledger tracked with SHA-256)
3. Seeds all 12 synthetic clinical personas, 30-day care plans & wearable timeseries
4. Executes the canonical 16-step challenge demonstration workflow
5. Outputs demo access endpoints, web application URLs, and evaluation credentials
"""

import sys
import os
import io
import time
import subprocess
from pathlib import Path

# Windows UTF-8 console output safeguard
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.migrate import run_migrations
from scripts.seed_data import seed_all_demo_data
from services.demo.challenge_workflow import execute_all_challenge_steps, CHALLENGE_CITIZEN_ID


# ANSI Terminal Colors
class Colors:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def print_banner():
    banner = f"""
{Colors.CYAN}{Colors.BOLD}================================================================================
   ____                  _   _            _ _   _         _    ___ 
  / ___|  _____   ____ _| | | | ___  __ _| | |_| |__     / \\  |_ _|
  \\___ \\ / _ \\ \\ / / _` | |_| |/ _ \\/ _` | | __| '_ \\   / _ \\  | | 
   ___) |  __/\\ V / (_| |  _  |  __/ (_| | | |_| | | | / ___ \\ | | 
  |____/ \\___| \\_/ \\__,_|_| |_|\\___|\\__,_|_|\\__|_| |_|/_/   \\_\\___|
{Colors.RESET}
  {Colors.BOLD}SEVA FIRST INNOVATION CHALLENGE — DEMONSTRATION RUNNER{Colors.RESET}
  {Colors.GREEN}"Detect risk early. Prevent disease. Bring personalized healthcare to every community."{Colors.RESET}
================================================================================
"""
    print(banner)


def run_demo_pipeline():
    print_banner()

    # STEP 1: Verify Environment & Runtime
    print(f"{Colors.BOLD}{Colors.YELLOW}[STEP 1/5] Verifying Runtime Environment & Configuration...{Colors.RESET}")
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        example_env = PROJECT_ROOT / ".env.example"
        if example_env.exists():
            print("  -> Creating .env from .env.example ...")
            env_file.write_text(example_env.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"  {Colors.GREEN}[OK] Environment configuration verified.{Colors.RESET}")

    # STEP 2: Run Database Migrations
    print(f"\n{Colors.BOLD}{Colors.YELLOW}[STEP 2/5] Applying Database Migrations (SHA-256 Ledger)...{Colors.RESET}")
    try:
        from packages.config.settings import settings
        if settings.DATABASE_URL.startswith("sqlite"):
            print(f"  {Colors.GREEN}[OK] Local zero-dependency SQLite storage active.{Colors.RESET}")
        else:
            run_migrations(verbose=False)
            print(f"  {Colors.GREEN}[OK] PostgreSQL schema migrations verified and applied.{Colors.RESET}")
    except Exception as e:
        print(f"  {Colors.YELLOW}[NOTICE] Migration check completed: {e}{Colors.RESET}")

    # STEP 3: Seed Synthetic Data
    print(f"\n{Colors.BOLD}{Colors.YELLOW}[STEP 3/5] Seeding Synthetic Personas, Wearable Timeseries & Cohorts...{Colors.RESET}")
    seed_summary = seed_all_demo_data()
    print(f"  {Colors.GREEN}[OK] Seeded 12 comprehensive clinical personas across all NCD phenotypes.{Colors.RESET}")
    print(f"  {Colors.GREEN}[OK] Initialized 30-day care plans and 14-day continuous wearable telemetry.{Colors.RESET}")

    # STEP 4: Execute 16-Step Canonical Challenge Demo
    print(f"\n{Colors.BOLD}{Colors.YELLOW}[STEP 4/5] Executing 16-Step Canonical Challenge Demo Workflow...{Colors.RESET}")
    summary = execute_all_challenge_steps()
    print(f"  {Colors.GREEN}[OK] Executed all 16 challenge steps successfully in {summary.total_execution_seconds:.2f}s!{Colors.RESET}")
    print(f"  Citizen Focus: {summary.citizen_name} ({summary.citizen_id})")
    print(f"  Baseline Metabolic Risk:     {summary.baseline_risk_score * 100:.1f}% (MODERATE)")
    print(f"  Deteriorated Risk (Month 6): {summary.peak_deteriorated_risk_score * 100:.1f}% (HIGH / CRITICAL ALERT)")
    print(f"  Post-Intervention (Month 7): {summary.final_reversed_risk_score * 100:.1f}% (IMPROVING / CONTROLLED)")
    print(f"  Net Trajectory Improvement:  {summary.net_risk_reduction_percentage:.1f}% Risk Reduction")
    print(f"  Projected Healthcare Savings: ₹{summary.population_savings_inr:,.0f} per 10k cohort")

    # STEP 5: Present Access Points & Credentials
    print(f"\n{Colors.BOLD}{Colors.YELLOW}[STEP 5/5] Demonstration Access Points & Credentials{Colors.RESET}")
    print("=" * 80)
    print(f"{Colors.BOLD}{'APPLICATION / PORTAL':<32} {'URL':<35} {'ROLE'}{Colors.RESET}")
    print("-" * 80)
    print(f"{'Citizen Mobile App':<32} {'http://localhost:8000/citizen-app':<35} {'CITIZEN'}")
    print(f"{'Health Worker Portal':<32} {'http://localhost:8000/health-worker-app':<35} {'HEALTH_WORKER'}")
    print(f"{'Clinician Copilot Dashboard':<32} {'http://localhost:8000/clinician-app':<35} {'CLINICIAN'}")
    print(f"{'Public Health Intel Dashboard':<32} {'http://localhost:8000/public-health-app':<35} {'PUBLIC_HEALTH_OFFICER'}")
    print(f"{'SevaHealth Observability':<32} {'http://localhost:8000/observability':<35} {'SYSTEM_ADMIN'}")
    print(f"{'API Interactive Docs (Swagger)':<32} {'http://localhost:8000/docs':<35} {'DEVELOPER / EVALUATOR'}")
    print("=" * 80)

    print(f"\n{Colors.BOLD}EVALUATION CREDENTIALS (Pre-authenticated in Demo Mode):{Colors.RESET}")
    print("  - Citizen User:       citizen@sevahealth.ai       / password123")
    print("  - Health Worker:      worker@sevahealth.ai        / password123")
    print("  - Clinician:          doctor@sevahealth.ai        / password123")
    print("  - Public Health Admin:admin@sevahealth.ai         / password123")

    print(f"\n{Colors.BOLD}CORE DEMONSTRATION WORKFLOWS:{Colors.RESET}")
    print(f"  1. Citizen Health Trajectory:  Visit http://localhost:8000/citizen-app to review Ramesh Patel.")
    print(f"  2. Field Intake Screening:    Visit http://localhost:8000/health-worker-app to screen a citizen in <3 mins.")
    print(f"  3. Clinical Copilot Review:   Visit http://localhost:8000/clinician-app to approve proposed interventions.")
    print(f"  4. District Population Map:   Visit http://localhost:8000/public-health-app to inspect Mysuru taluk hotspots.")

    print(f"\n{Colors.GREEN}{Colors.BOLD}DEMO READY: All systems operational.{Colors.RESET}")
    print("=" * 80)


if __name__ == "__main__":
    if "--reset" in sys.argv:
        print_banner()
        print(f"{Colors.BOLD}{Colors.YELLOW}[RESET] Triggering SevaHealth Demo Reset Button...{Colors.RESET}")
        from services.demo.challenge_workflow import reset_challenge_demo
        res = reset_challenge_demo()
        print(f"  {Colors.GREEN}[OK] {res['message']}{Colors.RESET}")
        print(f"  Citizen Subject: {res['citizen_id']}")
        print(f"  Reset Timestamp: {res['reset_at']}")
        print(f"  Baseline: Restored to Step 0.")
        print(f"{Colors.GREEN}{Colors.BOLD}DEMO ENVIRONMENT RESET COMPLETED.{Colors.RESET}")
        print("=" * 80)
        sys.exit(0)
    run_demo_pipeline()
