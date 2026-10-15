-- ==============================================================================
-- SevaHealth AI: Complete 32 Relational Tables Schema (SQLite Engine)
-- File: scripts/create_all_32_tables_sqlite.sql
-- All 32 Tables with Primary Keys, Foreign Keys, Check Constraints & Indexes
-- ==============================================================================

PRAGMA foreign_keys = ON;

-- 1. Organizations
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'STATE_HEALTH_MISSION',
    jurisdiction TEXT NOT NULL DEFAULT 'Karnataka',
    parent_organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 2. Care Teams
CREATE TABLE IF NOT EXISTS care_teams (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 3. Care Team Members
CREATE TABLE IF NOT EXISTS care_team_members (
    id TEXT PRIMARY KEY,
    care_team_id TEXT NOT NULL REFERENCES care_teams(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 4. Patients
CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    abha_id TEXT UNIQUE,
    user_id TEXT UNIQUE,
    primary_care_team_id TEXT REFERENCES care_teams(id) ON DELETE SET NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 5. Profiles
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL UNIQUE REFERENCES patients(id) ON DELETE CASCADE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    birth_date TEXT NOT NULL,
    gender TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT,
    address_line TEXT,
    state TEXT NOT NULL DEFAULT 'Karnataka',
    district TEXT NOT NULL DEFAULT 'Mysuru',
    sub_district TEXT NOT NULL DEFAULT 'Nanjangud',
    village_or_ward TEXT NOT NULL DEFAULT 'Ward 4',
    primary_language TEXT NOT NULL DEFAULT 'en',
    socioeconomic_tier TEXT DEFAULT 'BPL',
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 6. Family History
CREATE TABLE IF NOT EXISTS family_histories (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    relative_relationship TEXT NOT NULL,
    condition_code TEXT NOT NULL,
    condition_name TEXT NOT NULL,
    age_at_onset INTEGER,
    is_deceased INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 7. Lifestyle Profiles
CREATE TABLE IF NOT EXISTS lifestyle_profiles (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    tobacco_use TEXT NOT NULL,
    alcohol_use TEXT NOT NULL,
    dietary_pattern TEXT NOT NULL,
    physical_activity_level TEXT NOT NULL,
    sleep_hours_per_night REAL NOT NULL,
    perceived_stress_level TEXT NOT NULL DEFAULT 'MODERATE',
    recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 8. Risk Factors
CREATE TABLE IF NOT EXISTS risk_factors (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    domain TEXT NOT NULL,
    name TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'MODERATE',
    identified_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    is_active INTEGER NOT NULL DEFAULT 1
);

-- 9. Vital Signs
CREATE TABLE IF NOT EXISTS vital_signs (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    clinical_type TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    measurement_timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT '{}',
    confidence_score REAL NOT NULL DEFAULT 1.00,
    is_flagged_abnormal INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 10. Lab Results
CREATE TABLE IF NOT EXISTS lab_results (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    test_name TEXT NOT NULL,
    loinc_code TEXT,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    reference_range TEXT NOT NULL,
    interpretation TEXT NOT NULL DEFAULT 'NORMAL',
    measurement_timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT '{}',
    confidence_score REAL NOT NULL DEFAULT 1.00,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 11. Medications
CREATE TABLE IF NOT EXISTS medications (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    drug_name TEXT NOT NULL,
    dosage TEXT NOT NULL,
    frequency TEXT NOT NULL,
    indication TEXT NOT NULL,
    prescribed_by TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 12. Medication Adherences
CREATE TABLE IF NOT EXISTS medication_adherences (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
    scheduled_date TEXT NOT NULL,
    taken INTEGER NOT NULL,
    reported_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    reason_skipped TEXT
);

-- 13. Screenings
CREATE TABLE IF NOT EXISTS screenings (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    screening_type TEXT NOT NULL,
    administered_by TEXT NOT NULL,
    administrator_role TEXT NOT NULL,
    location TEXT NOT NULL,
    screened_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 14. Screening Results
CREATE TABLE IF NOT EXISTS screening_results (
    id TEXT PRIMARY KEY,
    screening_id TEXT NOT NULL UNIQUE REFERENCES screenings(id) ON DELETE CASCADE,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    cbac_score INTEGER,
    idrs_score INTEGER,
    needs_immediate_referral INTEGER NOT NULL DEFAULT 0,
    summary_findings TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 15. Risk Assessments
CREATE TABLE IF NOT EXISTS risk_assessments (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    screening_id TEXT REFERENCES screenings(id) ON DELETE SET NULL,
    overall_score REAL NOT NULL,
    overall_tier TEXT NOT NULL,
    domain_scores TEXT NOT NULL DEFAULT '{}',
    clinical_safety_disclaimer TEXT NOT NULL,
    assessed_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 16. Risk Factor Contributions
CREATE TABLE IF NOT EXISTS risk_factor_contributions (
    id TEXT PRIMARY KEY,
    risk_assessment_id TEXT NOT NULL REFERENCES risk_assessments(id) ON DELETE CASCADE,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    factor_name TEXT NOT NULL,
    factor_category TEXT NOT NULL,
    observed_value TEXT NOT NULL,
    target_value TEXT NOT NULL,
    relative_weight REAL NOT NULL,
    is_protective INTEGER NOT NULL DEFAULT 0,
    evidence_guideline TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 17. Risk Trajectories
CREATE TABLE IF NOT EXISTS risk_trajectories (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    assessment_ids TEXT NOT NULL DEFAULT '[]',
    historical_scores TEXT NOT NULL DEFAULT '[]',
    trend TEXT NOT NULL,
    rate_of_change REAL NOT NULL DEFAULT 0.0,
    projected_tier_6m TEXT NOT NULL,
    evaluated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 18. Intervention Plans
CREATE TABLE IF NOT EXISTS intervention_plans (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    risk_assessment_id TEXT REFERENCES risk_assessments(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    primary_domain TEXT NOT NULL,
    duration_days INTEGER NOT NULL DEFAULT 30,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    adherence_rate REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 19. Interventions
CREATE TABLE IF NOT EXISTS interventions (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES intervention_plans(id) ON DELETE CASCADE,
    pillar TEXT NOT NULL,
    title TEXT NOT NULL,
    prescription_text TEXT NOT NULL,
    frequency TEXT NOT NULL,
    target_metric TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 20. Goals
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    plan_id TEXT REFERENCES intervention_plans(id) ON DELETE SET NULL,
    metric_name TEXT NOT NULL,
    baseline_value REAL NOT NULL,
    target_value REAL NOT NULL,
    target_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 21. Check-Ins
CREATE TABLE IF NOT EXISTS check_ins (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL REFERENCES intervention_plans(id) ON DELETE CASCADE,
    check_in_date TEXT NOT NULL,
    tasks_completed_count INTEGER NOT NULL DEFAULT 0,
    tasks_total_count INTEGER NOT NULL DEFAULT 0,
    subjective_wellbeing TEXT NOT NULL DEFAULT 'GOOD',
    recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 22. Wearable Connections
CREATE TABLE IF NOT EXISTS wearable_connections (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    device_brand TEXT NOT NULL,
    device_model TEXT NOT NULL,
    connection_status TEXT NOT NULL DEFAULT 'CONNECTED',
    last_synced_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 23. Wearable Observations
CREATE TABLE IF NOT EXISTS wearable_observations (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    device_connection_id TEXT NOT NULL REFERENCES wearable_connections(id) ON DELETE CASCADE,
    metric_type TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    measurement_timestamp TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'SMARTWATCH_WEARABLE_SDK',
    provenance TEXT NOT NULL DEFAULT '{}',
    confidence_score REAL NOT NULL DEFAULT 1.00,
    rolling_7d_baseline REAL,
    baseline_deviation_sigma REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 24. Clinical Encounters
CREATE TABLE IF NOT EXISTS clinical_encounters (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    clinician_id TEXT NOT NULL,
    encounter_type TEXT NOT NULL,
    reason_for_visit TEXT NOT NULL,
    soap_subjective TEXT NOT NULL,
    soap_objective TEXT NOT NULL,
    soap_assessment TEXT NOT NULL,
    soap_plan TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    started_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    ended_at TEXT
);

-- 25. Care Plans
CREATE TABLE IF NOT EXISTS care_plans (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    care_team_id TEXT REFERENCES care_teams(id) ON DELETE SET NULL,
    lead_clinician_id TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 26. Referrals
CREATE TABLE IF NOT EXISTS referrals (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    referring_worker_id TEXT NOT NULL,
    referred_to_facility TEXT NOT NULL,
    urgency TEXT NOT NULL DEFAULT 'ROUTINE',
    clinical_reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    completed_at TEXT
);

-- 27. Alerts
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    urgency TEXT NOT NULL DEFAULT 'PRIORITY',
    alert_type TEXT NOT NULL,
    message TEXT NOT NULL,
    clinical_rule_triggered TEXT NOT NULL,
    is_acknowledged INTEGER NOT NULL DEFAULT 0,
    acknowledged_by TEXT,
    acknowledged_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 28. Notifications
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    recipient_user_id TEXT NOT NULL,
    patient_id TEXT REFERENCES patients(id) ON DELETE SET NULL,
    channel TEXT NOT NULL DEFAULT 'IN_APP',
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 29. Consents
CREATE TABLE IF NOT EXISTS consents (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    grantee_id TEXT NOT NULL,
    grantee_role TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'CARE_DELIVERY',
    scope_data_types TEXT NOT NULL DEFAULT '["VITALS","LABS","WEARABLES","RISK_ASSESSMENTS"]',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    valid_from TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    valid_until TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 30. Documents
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    file_name TEXT NOT NULL,
    file_type TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    sha256_hash TEXT NOT NULL,
    uploaded_by TEXT NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 31. Document References
CREATE TABLE IF NOT EXISTS document_references (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    doc_type TEXT NOT NULL,
    clinical_summary TEXT,
    parsed_entities_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- 32. Audit Events
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    organization_id TEXT REFERENCES organizations(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    patient_id TEXT REFERENCES patients(id) ON DELETE SET NULL,
    ip_address TEXT,
    user_agent TEXT,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

-- ==============================================================================
-- INDEXES
-- ==============================================================================
CREATE INDEX IF NOT EXISTS idx_profiles_geography ON profiles(state, district, sub_district, village_or_ward);
CREATE INDEX IF NOT EXISTS idx_vital_signs_patient_time ON vital_signs(patient_id, measurement_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_lab_results_patient_time ON lab_results(patient_id, measurement_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_wearable_obs_patient_time ON wearable_observations(patient_id, measurement_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_screenings_patient ON screenings(patient_id, screened_at DESC);
CREATE INDEX IF NOT EXISTS idx_risk_assessments_patient ON risk_assessments(patient_id, assessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_intervention_plans_patient ON intervention_plans(patient_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_clinical_encounters_patient ON clinical_encounters(patient_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_patient ON alerts(patient_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_consents_patient ON consents(patient_id, status);
CREATE INDEX IF NOT EXISTS idx_audit_events_timestamp ON audit_events(created_at DESC);
