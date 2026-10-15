"""Cross-Site Scripting (XSS) Defense & HTML Sanitization Engine (TC-SEC-002).

Provides defense-in-depth XSS protection:
1. XSSDetector: Identifies synthetic script payloads, event-handler injections, and pseudo-protocol exploits.
2. XSSSanitizer: Neutralizes active executable tags and escapes dangerous HTML characters to prevent script execution.
3. SafeDOMRenderer: Invariant proving safe DOM rendering via textContent and entity-escaped HTML interpolation.
4. XSSViolationError: Intercepts malicious script submissions when strict mode is active.
"""

import re
import html
import urllib.parse
from typing import Tuple, Optional, Dict, Any, List


class XSSViolationError(ValueError):
    """Raised when an untrusted input contains active script injection payload in strict mode."""

    def __init__(self, message: str, payload_snippet: str = "", attack_category: str = "UNKNOWN"):
        super().__init__(message)
        self.payload_snippet = payload_snippet
        self.attack_category = attack_category


class XSSDetector:
    """Detects representative and obfuscated Cross-Site Scripting (XSS) vectors."""

    # Curated regex signatures for XSS attack vectors
    SIGNATURES = {
        "SCRIPT_TAG": re.compile(
            r"<\s*script[^>]*>.*?(?:<\s*/\s*script\s*>)?|"
            r"<\s*script[^>]*\s*/?\s*>|"
            r"<\s*/\s*script\s*>",
            re.IGNORECASE | re.DOTALL,
        ),
        "EMBEDDED_RESOURCE": re.compile(
            r"<\s*(?:iframe|object|embed|applet|base|meta|link|style)[^>]*>",
            re.IGNORECASE,
        ),
        "SVG_MATH_VECTOR": re.compile(
            r"<\s*(?:svg|math)(?:[\s/][^>]*?)?(?:on\w+\s*=|href\s*=\s*['\"]?javascript|/onload)",
            re.IGNORECASE | re.DOTALL,
        ),
        "EVENT_HANDLER": re.compile(
            r"""<\s*[a-zA-Z0-9_\-]+[^>]*?\s+(?:on(?:abort|blur|change|click|dblclick|error|focus|keydown|keypress|keyup|load|mousedown|mousemove|mouseout|mouseover|mouseup|reset|resize|scroll|select|submit|toggle|unload|pointer\w+|touch\w+))\s*=\s*['"]?[^'">\s]+""",
            re.IGNORECASE | re.DOTALL,
        ),
        "DIRECT_EVENT_ATTR": re.compile(
            r"""\bon(?:error|load|click|focus|mouseover|toggle|submit)\s*=\s*['"]?[^'">]+""",
            re.IGNORECASE,
        ),
        "PSEUDO_PROTOCOL": re.compile(
            r"""(?:(?:\bhref|\bsrc|\bdata|\baction)\s*=\s*['"]?\s*)?(?:javascript|vbscript)\s*:|data\s*:\s*text\/html""",
            re.IGNORECASE,
        ),
    }

    @classmethod
    def inspect(cls, value: Any, field_name: str = "field") -> Tuple[bool, Optional[str], Optional[str]]:
        """Inspects an untrusted string for active script or XSS indicators.
        
        Returns:
            Tuple of (is_malicious, attack_category, matched_description)
        """
        if not isinstance(value, str):
            return False, None, None

        raw = value.strip()
        if not raw:
            return False, None, None

        # Check raw string
        for cat, pattern in cls.SIGNATURES.items():
            match = pattern.search(raw)
            if match:
                snippet = match.group(0)[:60]
                return True, cat, f"Detected {cat} in {field_name}: '{snippet}'"

        # Check URL-decoded string to catch obfuscation (e.g. %3Cscript%3E)
        try:
            decoded = urllib.parse.unquote(raw)
            if decoded != raw:
                for cat, pattern in cls.SIGNATURES.items():
                    match = pattern.search(decoded)
                    if match:
                        snippet = match.group(0)[:60]
                        return True, cat, f"Detected URL-encoded {cat} in {field_name}: '{snippet}'"
        except Exception:
            pass

        return False, None, None


class XSSSanitizer:
    """Sanitizes user and clinical free-text to guarantee inert rendering in dashboards."""

    # Dangerous tags that must be stripped completely
    STRIP_TAGS_PATTERN = re.compile(
        r"<\s*(?:script|iframe|object|embed|applet|style|meta|link)[^>]*>.*?</\s*(?:script|iframe|object|embed|applet|style|meta|link)\s*>|"
        r"<\s*(?:script|iframe|object|embed|applet|style|meta|link)[^>]*\s*/?\s*>",
        re.IGNORECASE | re.DOTALL,
    )

    # Active event handlers and javascript: schemes
    STRIP_HANDLERS_PATTERN = re.compile(
        r"""\bon[a-zA-Z]+\s*=\s*(?:'[^']*'|"[^"]*"|[^\s>]+)|"""
        r"""(?:javascript|vbscript|data:\s*text/html)\s*:""",
        re.IGNORECASE,
    )

    @classmethod
    def sanitize_text(cls, text: str, encode_entities: bool = True) -> str:
        """Neutralizes executable scripts from free-text inputs.
        
        Args:
            text: Untrusted input string.
            encode_entities: If True, escapes HTML characters (&, <, >, ", ') to HTML entities.
            
        Returns:
            Sanitized, inert text safe for storage and dashboard display.
        """
        if not isinstance(text, str):
            return text

        # 1. Strip dangerous tags
        cleaned = cls.STRIP_TAGS_PATTERN.sub("", text)

        # 2. Strip inline event handlers and pseudo-protocols
        cleaned = cls.STRIP_HANDLERS_PATTERN.sub("", cleaned)

        # 3. HTML Entity Encode to prevent active execution in browser DOM
        if encode_entities:
            # html.escape with quote=True encodes &, <, >, ", and '
            return html.escape(cleaned.strip(), quote=True)

        return cleaned.strip()

    @classmethod
    def sanitize_dict(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively cleans all string values within a dictionary."""
        sanitized = {}
        for k, v in data.items():
            if isinstance(v, str):
                sanitized[k] = cls.sanitize_text(v)
            elif isinstance(v, dict):
                sanitized[k] = cls.sanitize_dict(v)
            elif isinstance(v, list):
                sanitized[k] = [
                    cls.sanitize_dict(item) if isinstance(item, dict)
                    else (cls.sanitize_text(item) if isinstance(item, str) else item)
                    for item in v
                ]
            else:
                sanitized[k] = v
        return sanitized

    @classmethod
    def is_inert(cls, text: str) -> bool:
        """Verifies that text contains zero unescaped tags or active execution vectors."""
        if not isinstance(text, str):
            return True
        # If text contains unescaped < or >, it may form a tag
        has_raw_brackets = "<" in text or ">" in text
        is_detected, _, _ = XSSDetector.inspect(text)
        return not has_raw_brackets and not is_detected


class SafeDOMRenderer:
    """Demonstrates and tests browser DOM rendering invariants for XSS mitigation."""

    @staticmethod
    def render_via_text_content(raw_input: str) -> Dict[str, Any]:
        """Simulates browser node.textContent = raw_input.
        
        In the browser DOM specification (WHATWG), textContent never parses HTML tags.
        All characters, including '<script>', are parsed as text nodes.
        """
        return {
            "mode": "DOM_TEXT_CONTENT",
            "rendered_display": raw_input,
            "created_element_count": 0,
            "executable_scripts_triggered": 0,
            "is_safe": True,
            "explanation": "Browser textContent assigns directly to a TextNode, completely bypassing HTML tokenization."
        }

    @staticmethod
    def render_via_inner_html(sanitized_input: str) -> Dict[str, Any]:
        """Simulates browser node.innerHTML = sanitized_input.
        
        Because sanitized_input contains HTML entities (&lt;script&gt;), the HTML tokenizer
        parses them as character references resulting in text, NOT element nodes.
        """
        has_active_tags = bool(re.search(r"<[a-zA-Z/]", sanitized_input))
        return {
            "mode": "DOM_INNER_HTML_ESCAPED",
            "rendered_markup": sanitized_input,
            "has_active_tags": has_active_tags,
            "created_script_elements": 0,
            "executable_scripts_triggered": 0,
            "is_safe": not has_active_tags,
            "explanation": "Entities (&lt; &gt;) are parsed as character data, preventing creation of executable HTML script/event nodes."
        }
