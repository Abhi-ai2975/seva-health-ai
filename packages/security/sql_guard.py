"""SQL Injection Defense, Query Parameterization Verification, and Input Inspection.

Ensures that all incoming parameters across search filters, clinical inputs,
authentication endpoints, and database queries are handled safely, queries remain
properly parameterized, and unauthorized data is never exposed (TC-SEC-001).
"""

import re
from typing import Optional, List, Dict, Any, Tuple
import structlog

logger = structlog.get_logger(__name__)


class SQLInjectionViolationError(Exception):
    """Raised when an input contains explicit SQL injection vectors."""

    def __init__(self, message: str, parameter: str = "unknown", payload: str = ""):
        super().__init__(message)
        self.parameter = parameter
        self.payload = payload


class SQLInjectionDetector:
    """Security Guard that inspects inputs for SQL injection patterns and enforces safe parameterization."""

    # Curated regex signatures for SQL injection attack patterns
    INJECTION_PATTERNS: List[Tuple[str, str]] = [
        # 1. Tautology / Auth bypass patterns: ' OR '1'='1, OR 1=1, OR true
        (r"(?i)\bOR\s+['\"]?1['\"]?\s*=\s*['\"]?1['\"]?", "Tautology authentication bypass (OR 1=1)"),
        (r"(?i)\bOR\s+['\"]?[a-zA-Z0-9]+['\"]?\s*=\s*['\"]?[a-zA-Z0-9]+['\"]?", "Tautology comparison bypass"),
        (r"(?i)\bOR\s+(?:true|1)\s*--", "Tautology comment bypass"),
        (r"(?i)['\"]\s*OR\s+['\"][^'\"]*['\"]\s*=\s*['\"]", "String tautology quote bypass"),

        # 2. UNION SELECT data exfiltration
        (r"(?i)\bUNION\s+(?:ALL\s+)?SELECT\b", "UNION-based data exfiltration query"),

        # 3. Stacked queries / DDL / Destructive commands
        (r"(?i);\s*(?:DROP|ALTER|TRUNCATE|DELETE\s+FROM|INSERT\s+INTO|UPDATE)\b", "Stacked destructive DDL/DML query"),
        (r"(?i)\bDROP\s+(?:TABLE|DATABASE|VIEW|INDEX)\b", "Destructive DROP command"),
        (r"(?i)\bTRUNCATE\s+TABLE\b", "Destructive TRUNCATE command"),

        # 4. Comment truncation vectors used to slice off trailing WHERE clauses
        (r"(?:--|/\*|\*/|;\s*--)", "SQL comment truncation syntax"),

        # 5. Blind / Time-based injection vectors
        (r"(?i)\b(?:SLEEP|BENCHMARK|WAITFOR\s+DELAY|PG_SLEEP)\s*\(", "Time-based blind SQL injection vector"),
        (r"(?i)\bAND\s+(?:SELECT|\(?\d+\s*=\s*\d+\)?)", "Boolean-based blind query injection"),

        # 6. Database meta / system functions & command execution
        (r"(?i)\b(?:EXEC|EXECUTE|xp_cmdshell|sp_executesql)\b", "Stored procedure / shell execution vector"),
        (r"(?i)\bCONVERT\s*\(\s*int\s*,", "Error-based data extraction vector"),
    ]

    @classmethod
    def inspect_value(
        cls,
        value: Any,
        param_name: str = "input",
        raise_on_detect: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Inspects an individual parameter for SQL injection syntax.
        
        Returns:
            Tuple of (is_malicious: bool, description: Optional[str])
        """
        if value is None or not isinstance(value, str):
            return False, None

        val_str = value.strip()
        if not val_str:
            return False, None

        for pattern, description in cls.INJECTION_PATTERNS:
            if re.search(pattern, val_str):
                logger.warning(
                    "SECURITY_ALERT: SQL injection payload detected",
                    parameter=param_name,
                    pattern=pattern,
                    description=description,
                    payload_snippet=val_str[:80],
                )
                if raise_on_detect:
                    raise SQLInjectionViolationError(
                        f"SQL injection syntax detected in parameter '{param_name}': {description}.",
                        parameter=param_name,
                        payload=val_str,
                    )
                return True, description

        return False, None

    @classmethod
    def inspect_dict(
        cls,
        data: Dict[str, Any],
        raise_on_detect: bool = False,
    ) -> List[Dict[str, Any]]:
        """Scans all string values in a dictionary for injection attempts."""
        findings = []
        for key, val in data.items():
            if isinstance(val, str):
                is_malicious, desc = cls.inspect_value(val, param_name=key, raise_on_detect=raise_on_detect)
                if is_malicious:
                    findings.append({
                        "parameter": key,
                        "description": desc,
                        "value": val,
                    })
            elif isinstance(val, dict):
                findings.extend(cls.inspect_dict(val, raise_on_detect=raise_on_detect))
        return findings

    @classmethod
    def is_safe_identifier(cls, identifier: str) -> bool:
        """Validates that a table or column name contains strictly alphanumeric and underscore characters."""
        return bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", identifier))


class SafeQueryExecutor:
    """Demonstrates and verifies parameterized query execution vs raw unparameterized string concatenation.

    Proves that parameter binding binds values as literal data types, neutralizes SQL syntax
    injection, and guarantees that unauthorized data is never exposed.
    """

    @classmethod
    def simulate_vulnerable_query(cls, raw_input: str) -> Tuple[str, str]:
        """Simulates what an unparameterized, vulnerable query string looks like."""
        vulnerable_sql = f"SELECT * FROM citizens WHERE id = '{raw_input}' AND tenant_id = 'karnataka_state_health'"
        return vulnerable_sql, "VULNERABLE_RAW_CONCATENATION"

    @classmethod
    def build_parameterized_query(
        cls,
        table: str,
        where_fields: List[str],
    ) -> Tuple[str, str]:
        """Builds a secure parameterized SQL template using named placeholders."""
        if not SQLInjectionDetector.is_safe_identifier(table):
            raise ValueError(f"Invalid table identifier: '{table}'")

        clauses = []
        for field in where_fields:
            if not SQLInjectionDetector.is_safe_identifier(field):
                raise ValueError(f"Invalid column identifier: '{field}'")
            clauses.append(f"{field} = :{field}")

        where_clause = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM {table} WHERE {where_clause}"
        return sql, "SECURE_PARAMETERIZED_BINDING"

    @classmethod
    def execute_in_memory_parameterized(
        cls,
        records: List[Dict[str, Any]],
        filter_field: str,
        bound_param_value: Any,
    ) -> List[Dict[str, Any]]:
        """Simulates exact SQL parameter binding over a dataset.
        
        Because the bound parameter is treated strictly as literal data (never as SQL syntax),
        a payload like `' OR '1'='1` or `'; DROP TABLE...` searches strictly for that literal
        exact string match, matching 0 rows instead of dumping the table!
        """
        results = []
        for rec in records:
            actual_val = rec.get(filter_field)
            # Literal exact comparison (safe parameter binding)
            if actual_val == bound_param_value:
                results.append(rec)
        return results
