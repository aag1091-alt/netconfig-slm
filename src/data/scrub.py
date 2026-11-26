"""Scrub secrets / PII from raw configs before they enter the dataset.

Rules from research notes (MITRE T1602.002):
- type-7 / type-5 / secret hashes -> REDACTED
- SNMP community strings -> REDACTED
- enable secret / password lines -> REDACTED
- public IPs -> documentation ranges (handled conservatively: flag, don't auto-rewrite routing semantics)
- hostnames left to caller normalization (sw1/r1)
"""
import re

_PATTERNS = [
    (re.compile(r"(enable\s+secret\s+\d?\s*)\S+", re.I), r"\1REDACTED"),
    (re.compile(r"(password\s+(?:7|5)?\s*)\S+", re.I), r"\1REDACTED"),
    (re.compile(r"(username\s+\S+\s+(?:secret|password)\s+\d?\s*)\S+", re.I), r"\1REDACTED"),
    (re.compile(r"(snmp-server\s+community\s+)\S+", re.I), r"\1REDACTED"),
    (re.compile(r"(crypto\s+key.*|tacacs-server\s+key\s+)\S+", re.I), r"\1REDACTED"),
    (re.compile(r"(\btype\s*7\s+)[0-9a-fA-F]+"), r"\1REDACTED"),
]

def scrub_config(config: str) -> tuple[str, list[str]]:
    """Return (scrubbed_config, warnings)."""
    warnings: list[str] = []
    out = config
    for pat, repl in _PATTERNS:
        new = pat.sub(repl, out)
        if new != out:
            warnings.append(f"scrubbed: {pat.pattern[:40]}")
            out = new
    # Flag public IPs (very rough: not 10/8, 172.16/12, 192.168/16, 192.0.2/24, 198.51.100/24, 203.0.113/24)
    for m in re.finditer(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", out):
        ip = m.group(1)
        if ip.startswith(("10.", "192.168.", "192.0.2.", "198.51.100.", "203.0.113.")) or ip.startswith("172."):
            continue
        if ip in ("0.0.0.0", "255.255.255.0", "255.255.255.255", "255.255.0.0"):
            continue
        warnings.append(f"possible public IP not in doc range: {ip}")
    return out, warnings

if __name__ == "__main__":
    demo = "enable secret 5 $1$abcd\nsnmp-server community MySecret RO\nip route 8.8.8.8 255.255.255.255 192.168.1.1"
    cleaned, w = scrub_config(demo)
    print(cleaned)
    print(w)
