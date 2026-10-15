"""Path Traversal Guard & Storage Confinement Engine (TC-SEC-003).

Provides:
- PathTraversalDetector: Detects relative traversal sequences, encoded variations,
  null bytes, and absolute system path injections.
- StorageVaultManager: Confines all document file operations strictly to authorized
  storage boundaries using canonical path verification.
- PathTraversalViolationError: Typed exception for unauthorized path escape attempts.
"""

import os
import re
import urllib.parse
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List


class PathTraversalViolationError(Exception):
    """Raised when an unauthorized path traversal or storage escape attempt is detected."""
    pass


class PathTraversalDetector:
    """Detects path traversal attacks across filenames, path parameters, and query inputs."""

    # Traversal patterns
    TRAVERSAL_REGEXES = [
        # Relative traversal sequences: ../, ..\, ....//
        (re.compile(r"(?:\.\.[\\/]+|\.[\\/]+\.\.)"), "RELATIVE_TRAVERSAL", "Relative directory traversal sequence ('../' or '..\\')"),
        (re.compile(r"\.{2,}"), "RELATIVE_TRAVERSAL", "Multiple consecutive dots ('..') indicative of path navigation"),
        # Encoded traversal: %2e%2e%2f, %252e%252e%252f, %c0%af, %u002e
        (re.compile(r"(?:%2e|%252e|\.){2}(?:%2f|%252f|%5c|%255c|[\\/])", re.IGNORECASE), "URL_ENCODED_TRAVERSAL", "URL-encoded directory traversal sequence"),
        (re.compile(r"%c0%af|%c1%9c|%u002e", re.IGNORECASE), "OVERLONG_UNICODE_TRAVERSAL", "Overlong UTF-8 / Unicode traversal encoding"),
        # Null bytes: \x00, %00
        (re.compile(r"(\x00|%00|\0)"), "NULL_BYTE_INJECTION", "Null byte poisoning sequence in file path"),
        # Absolute system paths (Unix)
        (re.compile(r"^(?:/etc/|/var/|/proc/|/sys/|/root/|/tmp/|/usr/|/bin/|/sbin/)", re.IGNORECASE), "ABSOLUTE_SYSTEM_PATH", "Targeting sensitive Unix root filesystem"),
        # Absolute system paths (Windows)
        (re.compile(r"^[a-zA-Z]:[\\/](?:windows|system32|winnt|boot|program files|users)", re.IGNORECASE), "ABSOLUTE_SYSTEM_PATH", "Targeting sensitive Windows system root"),
        # UNC shares (\\server\share)
        (re.compile(r"^\\\\[\w\-]+[\\/]", re.IGNORECASE), "WINDOWS_UNC_PATH", "Windows Universal Naming Convention (UNC) share traversal"),
        # DOS device names
        (re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.IGNORECASE), "SPECIAL_DEVICE_NAME", "Reserved operating system device name"),
    ]

    @classmethod
    def inspect(cls, raw_path: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """Inspects raw path or filename for traversal patterns.

        Returns: (is_malicious, category, description)
        """
        if not raw_path or not isinstance(raw_path, str):
            return False, None, None

        # Check raw string
        for pattern, category, description in cls.TRAVERSAL_REGEXES:
            if pattern.search(raw_path):
                return True, category, description

        # Check URL-decoded string (one pass)
        try:
            decoded = urllib.parse.unquote(raw_path)
            if decoded != raw_path:
                for pattern, category, description in cls.TRAVERSAL_REGEXES:
                    if pattern.search(decoded):
                        return True, category, f"URL-decoded match: {description}"
        except Exception:
            pass

        # Check double-decoded string
        try:
            double_decoded = urllib.parse.unquote(urllib.parse.unquote(raw_path))
            if double_decoded != raw_path and double_decoded != decoded:
                for pattern, category, description in cls.TRAVERSAL_REGEXES:
                    if pattern.search(double_decoded):
                        return True, category, f"Double URL-decoded match: {description}"
        except Exception:
            pass

        # Explicit check for common sensitive targets
        sensitive_targets = [
            "etc/passwd", "etc/shadow", "etc/hosts", "windows/win.ini",
            "system32/cmd.exe", "boot.ini", ".env", "main.py", "settings.py"
        ]
        lower_raw = raw_path.lower().replace("\\", "/")
        for target in sensitive_targets:
            if target in lower_raw and (".." in lower_raw or lower_raw.startswith("/")):
                return True, "SENSITIVE_SYSTEM_TARGET", f"Attempting to target sensitive system file '{target}'"

        return False, None, None


class StorageVaultManager:
    """Enforces strict storage confinement on all document vault operations."""

    # Base storage directory
    _VAULT_ROOT = Path(os.getcwd()).resolve() / "storage" / "vault"

    @classmethod
    def get_vault_root(cls) -> Path:
        """Returns the canonical root directory of the authorized storage vault."""
        cls._VAULT_ROOT.mkdir(parents=True, exist_ok=True)
        return cls._VAULT_ROOT.resolve()

    @classmethod
    def sanitize_filename(cls, filename: str, strict: bool = False) -> str:
        """Sanitizes filename and strips path traversal components.

        In strict mode, raises PathTraversalViolationError on traversal sequences.
        In normal mode, strips directory components to retain a clean basename.
        """
        if not filename or not isinstance(filename, str):
            return "uploaded_document"

        # Check null byte
        if "\x00" in filename or "%00" in filename:
            raise PathTraversalViolationError("Null byte injection detected in filename.")

        # Decode URL encoding
        try:
            decoded = urllib.parse.unquote(filename)
        except Exception:
            decoded = filename

        is_malicious, cat, desc = PathTraversalDetector.inspect(decoded)
        if strict and is_malicious:
            raise PathTraversalViolationError(f"Prohibited path traversal sequence in filename: {desc}")

        # Strip directory traversal sequences and extract base name
        normalized = decoded.replace("\\", "/")
        base_name = os.path.basename(normalized)

        # Remove dangerous characters, keeping alphanumeric, dots, dashes, underscores
        safe_name = re.sub(r"[^\w\.\-]", "_", base_name)
        if not safe_name or safe_name == "." or safe_name == "..":
            safe_name = "sanitized_document"
        elif safe_name.startswith("."):
            safe_name = f"doc_{safe_name.lstrip('.')}"

        return safe_name

    @classmethod
    def resolve_and_confine(
        cls,
        untrusted_path: str,
        citizen_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Path:
        """Resolves an untrusted path and strictly confines it to the authorized storage boundary.

        Raises PathTraversalViolationError if the resolved path points outside the authorized root.
        """
        vault_root = cls.get_vault_root()

        if not untrusted_path or not isinstance(untrusted_path, str):
            raise PathTraversalViolationError("Empty or invalid file path provided.")

        # Check for null bytes
        if "\x00" in untrusted_path or "%00" in untrusted_path:
            raise PathTraversalViolationError("Null byte injection detected in file path.")

        # Inspect with detector
        is_malicious, cat, desc = PathTraversalDetector.inspect(untrusted_path)
        if is_malicious and cat in ("ABSOLUTE_SYSTEM_PATH", "WINDOWS_UNC_PATH", "SPECIAL_DEVICE_NAME"):
            raise PathTraversalViolationError(f"Unauthorized path rejected: {desc}")

        # Clean leading slashes if virtual vault path e.g. "/storage/vault/cit-01/..."
        clean_virtual = untrusted_path.replace("\\", "/")
        if clean_virtual.startswith("/storage/vault/"):
            clean_virtual = clean_virtual[len("/storage/vault/"):]
        elif clean_virtual.startswith("storage/vault/"):
            clean_virtual = clean_virtual[len("storage/vault/"):]
        elif clean_virtual.startswith("/"):
            # Attempted root escape
            raise PathTraversalViolationError(f"Absolute path '{untrusted_path}' is outside authorized storage vault.")

        # Define boundary directory
        if citizen_id:
            # Citizen boundary
            safe_citizen = cls.sanitize_filename(citizen_id, strict=False)
            boundary_dir = (vault_root / safe_citizen).resolve()
        else:
            boundary_dir = vault_root

        # Resolve target canonical path
        target_path = (boundary_dir / clean_virtual).resolve()

        # CONFINEMENT INVARIANT CHECK:
        # target_path must be a child of boundary_dir
        try:
            # Python 3.9+ is_relative_to
            is_confined = target_path.is_relative_to(boundary_dir)
        except AttributeError:
            is_confined = os.path.commonpath([str(target_path), str(boundary_dir)]) == str(boundary_dir)

        if not is_confined:
            raise PathTraversalViolationError(
                f"Path traversal detected: Resolved path '{target_path}' escapes authorized storage boundary '{boundary_dir}'."
            )

        return target_path

    @classmethod
    def get_safe_storage_path(
        cls,
        citizen_id: str,
        doc_id: str,
        filename: str,
    ) -> Tuple[str, Path]:
        """Generates a safe storage path guaranteed to be confined within the citizen's vault.

        Returns: (virtual_storage_path, physical_canonical_path)
        """
        vault_root = cls.get_vault_root()
        safe_citizen = cls.sanitize_filename(citizen_id, strict=False)
        safe_doc_id = cls.sanitize_filename(doc_id, strict=False)
        safe_filename = cls.sanitize_filename(filename, strict=False)

        virtual_path = f"/storage/vault/{safe_citizen}/{safe_doc_id}/{safe_filename}"
        citizen_vault = (vault_root / safe_citizen / safe_doc_id).resolve()
        citizen_vault.mkdir(parents=True, exist_ok=True)

        physical_path = (citizen_vault / safe_filename).resolve()

        # Verify confinement
        if not physical_path.is_relative_to(vault_root):
            raise PathTraversalViolationError("Failed storage confinement invariant on write.")

        return virtual_path, physical_path
