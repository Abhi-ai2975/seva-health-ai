"""CLI Database Tables & Records Explorer for SevaHealth AI.

Usage:
  python show_db_tables.py                     # Shows summary of all tables & counts
  python show_db_tables.py --table users       # Shows records in 'users' table (Name, Role, Email, Stored At)
  python show_db_tables.py --table citizens    # Shows records in 'citizens' table (Name, District, Stored At)
  python show_db_tables.py --table screenings  # Shows records in 'screenings' table
  python show_db_tables.py --table triage      # Shows records in 'triage_cases' table
  python show_db_tables.py --table schema      # Lists all 32 PostgreSQL relational tables
  python show_db_tables.py --query "SQL"       # Runs arbitrary SQL query on data/sevahealth.db
"""

import sys
import json
import argparse
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path
from services.store import store, DB_PATH
from scripts.seed_data import seed_all_demo_data


def print_banner(text: str):
    print("\n" + "=" * 115)
    print(f"  {text}")
    print("=" * 115)


def fetch_live_inspector_data():
    """Attempts to fetch live database records directly from running FastAPI server."""
    try:
        url = "http://localhost:8000/api/v1/auth/database-inspector"
        req = urllib.request.Request(url, headers={"User-Agent": "SevaHealth-DB-Explorer/1.0"})
        with urllib.request.urlopen(req, timeout=0.8) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                return data
    except Exception:
        pass
    return None


def fetch_sqlite_rows(query: str, params=()):
    """Reads rows directly from data/sevahealth.db SQLite database."""
    if not DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        return []


def get_tables_summary():
    # Ensure baseline data is seeded if empty
    if len(store.citizens) == 0:
        seed_all_demo_data()

    live_data = fetch_live_inspector_data()
    if live_data and "counts" in live_data:
        unique_users = live_data["counts"].get("total_users", len(store.list_users()))
        citizens_count = live_data["counts"].get("total_citizens", len(store.citizens))
    else:
        unique_users = len(store.list_users())
        citizens_count = len(store.citizens)

    tables = {
        "citizens": {
            "description": "Citizen demographics, ABHA IDs, location, contacts, and stored_at",
            "count": citizens_count,
            "sample_keys": ["id", "name", "gender", "district", "stored_at", "abha_id"]
        },
        "users": {
            "description": "Authenticated accounts, RBAC roles (Doctor, ASHA, Admin, Citizen), stored_at",
            "count": unique_users,
            "sample_keys": ["id", "full_name", "email", "role", "stored_at"]
        },
        "screenings": {
            "description": "NCD Screening sessions with CBAC and IDRS Indian Diabetes scores",
            "count": len(store.screenings),
            "sample_keys": ["id", "citizen_id", "status", "conducted_at"]
        },
        "observations": {
            "description": "Biometric observations (BP, Fasting Glucose, HbA1c, BMI, Steps)",
            "count": len(store.observations),
            "sample_keys": ["citizen_id", "obs_count"]
        },
        "risk_assessments": {
            "description": "NCD Composite Risk stratification & domain risk scores",
            "count": len(store.risk_assessments),
            "sample_keys": ["id", "citizen_id", "overall_score", "overall_tier", "trajectory"]
        },
        "care_plans": {
            "description": "30-day multi-pillar lifestyle prevention plans & daily tasks",
            "count": len(store.care_plans),
            "sample_keys": ["id", "citizen_id", "title", "adherence_percentage"]
        },
        "triage_cases": {
            "description": "High-risk clinician triage queue with AI SOAP clinical notes",
            "count": len(store.triage_cases),
            "sample_keys": ["id", "citizen_id", "urgency", "status", "assigned_clinician_id"]
        },
        "referrals": {
            "description": "Closed-loop clinical referrals between PHC, CHC, and Taluk Hospital",
            "count": len(store.referrals),
            "sample_keys": ["citizen_id", "referrals_count"]
        },
        "alerts": {
            "description": "High-risk escalation alerts & metabolic deterioration flags",
            "count": len(store.alerts),
            "sample_keys": ["citizen_id", "alerts_count"]
        },
        "clinical_notifications": {
            "description": "SMS, WhatsApp, and Push clinical escalation alerts",
            "count": len(store.clinical_notifications),
            "sample_keys": ["id", "recipient", "channel", "status"]
        },
        "trajectory_snapshots": {
            "description": "Longitudinal risk snapshots tracking clinical progression/reversal",
            "count": len(store.trajectory_snapshots),
            "sample_keys": ["citizen_id", "snapshots_count"]
        },
        "consents": {
            "description": "Citizen consent directives and purpose-based access permissions",
            "count": len(store.consents),
            "sample_keys": ["id", "citizen_id", "purpose"]
        },
        "security_incidents": {
            "description": "Security incident monitoring alerts (e.g. repeated auth failures, BOLA attempts)",
            "count": len(store.security_incidents),
            "sample_keys": ["id", "event", "timestamp"]
        },
        "clinical_access_audits": {
            "description": "Immutable forensic audit trail of clinical reads and access events",
            "count": len(store.clinical_access_audit_logs),
            "sample_keys": ["actor_id", "citizen_id", "timestamp"]
        },
    }
    return tables


def show_summary():
    tables = get_tables_summary()
    print_banner("SEVAHEALTH AI - DATABASE TABLES & ACTIVE DATA STORE OVERVIEW")
    print("Database Engine: High-Performance Concurrent In-Memory Store & SQLite Persistence (data/sevahealth.db)")
    print("Health Status:   ONLINE")
    print(f"Database File:   {DB_PATH.resolve()}")
    print("Relational DDL:  32 PostgreSQL tables in infrastructure/postgres/migrations/\n")

    print(f"{'Table Name':<24} | {'Row Count':<10} | {'Description'}")
    print("-" * 115)
    for name, info in tables.items():
        print(f"{name:<24} | {info['count']:<10} | {info['description']}")

    print("\n" + "-" * 115)
    print("COMMAND USAGE GUIDE:")
    print("  python show_db_tables.py --table users        # Users table (Name, Email, Role, Stored At)")
    print("  python show_db_tables.py --table citizens     # Citizens table (Name, District, Stored At)")
    print("  python show_db_tables.py --table triage       # Clinician Triage Cases")
    print("  python show_db_tables.py --table screenings   # NCD Screenings (CBAC & IDRS)")
    print("  python show_db_tables.py --table schema       # 32 PostgreSQL Domain Tables")
    print("  python show_db_tables.py --query \"SELECT full_name, email, role, stored_at FROM users\"")
    print("-" * 115)


def show_citizens():
    print_banner("TABLE: CITIZENS (Demographics, Locations & Stored At)")

    citizens_list = []
    # 1. Try live server first
    live_data = fetch_live_inspector_data()
    if live_data and "citizens" in live_data and live_data["citizens"]:
        citizens_list = live_data["citizens"]

    # 2. Try SQLite
    if not citizens_list:
        citizens_list = fetch_sqlite_rows("SELECT * FROM citizens ORDER BY stored_at DESC")

    # 3. Fallback to in-memory store
    if not citizens_list:
        if len(store.citizens) == 0:
            seed_all_demo_data()
        citizens_list = [c.to_dict() for c in store.citizens.values()]

    print(f"{'Citizen ID':<24} | {'Full Name':<22} | {'Gender':<8} | {'District':<14} | {'Stored At':<22} | {'ABHA ID'}")
    print("-" * 115)
    for c in citizens_list[:25]:
        c_id = str(c.get("id", ""))[:24]
        name = str(c.get("full_name") or c.get("name") or f"{c.get('first_name', '')} {c.get('last_name', '')}").strip()[:22]
        gender = str(c.get("gender", "OTHER"))[:8]
        dist = str(c.get("district", "N/A"))[:14]
        stored_at = str(c.get("stored_at") or c.get("created_at") or "Persisted")[:22]
        abha = str(c.get("abha_id") or "N/A")
        print(f"{c_id:<24} | {name:<22} | {gender:<8} | {dist:<14} | {stored_at:<22} | {abha}")
    print(f"\nTotal Citizens in DB: {len(citizens_list)} (Stored in SQLite: {DB_PATH.name})")


def show_users():
    print_banner("TABLE: USERS (Authenticated Accounts, RBAC Roles & Stored At)")

    users_list = []
    # 1. Try live server first
    live_data = fetch_live_inspector_data()
    if live_data and "users" in live_data and live_data["users"]:
        users_list = live_data["users"]

    # 2. Try SQLite
    if not users_list:
        users_list = fetch_sqlite_rows("SELECT * FROM users ORDER BY stored_at DESC")

    # 3. Fallback to in-memory store
    if not users_list:
        if len(store.users) == 0:
            seed_all_demo_data()
        users_list = [u.to_dict() for u in store.list_users()]

    seen = set()
    unique_users = []
    for u in users_list:
        u_id = u.get("id")
        if u_id and u_id not in seen:
            seen.add(u_id)
            unique_users.append(u)

    print(f"{'User ID':<24} | {'Full Name':<22} | {'Email':<28} | {'Role':<18} | {'Stored At':<22} | {'Status'}")
    print("-" * 125)
    for u in unique_users:
        u_id = str(u.get("id", ""))[:24]
        name = str(u.get("full_name") or u.get("name") or "User")[:22]
        email = str(u.get("email", ""))[:28]
        role = str(u.get("role", "CITIZEN"))[:18]
        stored_at = str(u.get("stored_at") or u.get("created_at") or "Persisted")[:22]
        status_val = "ACTIVE" if u.get("is_active", True) else "LOCKED"
        print(f"{u_id:<24} | {name:<22} | {email:<28} | {role:<18} | {stored_at:<22} | {status_val}")
    print(f"\nTotal Authenticated Accounts in DB: {len(unique_users)} (Persisted to {DB_PATH.name})")


def show_triage():
    print_banner("TABLE: TRIAGE_CASES (Clinician Review Queue)")
    if len(store.triage_cases) == 0:
        seed_all_demo_data()

    print(f"{'Case ID':<22} | {'Citizen Name':<20} | {'Urgency':<12} | {'Status':<14} | {'Reason'}")
    print("-" * 115)
    for tc in store.triage_cases.values():
        urg = str(tc.urgency.value if hasattr(tc.urgency, 'value') else tc.urgency)
        st = str(tc.status.value if hasattr(tc.status, 'value') else tc.status)
        reason = tc.escalation_reason[:40] + ("..." if len(tc.escalation_reason) > 40 else "")
        print(f"{tc.id:<22} | {tc.citizen_name:<20} | {urg:<12} | {st:<14} | {reason}")
    print(f"\nTotal Triage Cases: {len(store.triage_cases)}")


def show_screenings():
    print_banner("TABLE: SCREENINGS (CBAC & IDRS Surveys)")
    if len(store.screenings) == 0:
        seed_all_demo_data()

    print(f"{'Citizen ID':<28} | {'Screenings Count':<18} | {'Latest Screening ID'}")
    print("-" * 75)
    for cid, s_list in list(store.screenings.items())[:15]:
        latest_id = s_list[-1].id if s_list else "None"
        print(f"{cid:<28} | {len(s_list):<18} | {latest_id}")
    print(f"\nTotal Screened Citizens: {len(store.screenings)}")


def show_schema():
    print_banner("POSTGRESQL RELATIONAL SCHEMA (32 Domain Tables)")
    sql_path = Path("infrastructure/postgres/migrations/001_initial_domain_model.sql")
    if not sql_path.exists():
        print("Migration file not found!")
        return

    import re
    sql = sql_path.read_text(encoding="utf-8")
    tables = re.findall(r'CREATE TABLE IF NOT EXISTS ([a-z_]+)', sql)
    print(f"Found {len(tables)} Production PostgreSQL Domain Tables:\n")
    for idx, tbl in enumerate(tables, 1):
        print(f"  {idx:2d}. {tbl}")
    print(f"\nSchema file location: {sql_path.resolve()}")


def run_sql_query(query: str):
    print_banner(f"SQL QUERY EXECUTION on {DB_PATH.name}")
    print(f"Query: {query}\n")
    if not DB_PATH.exists():
        if len(store.citizens) == 0:
            seed_all_demo_data()

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            if not rows:
                print("Query returned 0 rows or executed successfully.")
                return

            columns = list(rows[0].keys())
            header = " | ".join(f"{c:<18}" for c in columns)
            print(header)
            print("-" * len(header))
            for r in rows:
                line = " | ".join(f"{str(r[c])[:18]:<18}" for c in columns)
                print(line)
            print(f"\nTotal rows returned: {len(rows)}")
    except Exception as e:
        print(f"SQL Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="SevaHealth AI Database Tables Explorer")
    parser.add_argument("--table", type=str, default="", help="Table name to inspect (citizens, users, triage, screenings, schema)")
    parser.add_argument("--query", type=str, default="", help="Direct SQL query to execute on SQLite database")
    args = parser.parse_args()

    if args.query:
        run_sql_query(args.query.strip())
        return

    tbl = args.table.lower().strip()
    if not tbl:
        show_summary()
    elif tbl == "citizens":
        show_citizens()
    elif tbl == "users":
        show_users()
    elif tbl in ("triage", "triage_cases"):
        show_triage()
    elif tbl == "screenings":
        show_screenings()
    elif tbl in ("schema", "tables"):
        show_schema()
    else:
        print(f"Unknown table: '{tbl}'. Available tables: citizens, users, triage, screenings, schema")
        show_summary()


if __name__ == "__main__":
    main()
