"""
Phase 19: Enterprise features — the pure logic.

Deliberately standard-library only (no SQLAlchemy/FastAPI imports) so every
decision here can be unit tested with a bare interpreter; the DB and
request plumbing that calls these functions lives in app/api/deps.py and
app/api/enterprise.py.

What "enterprise" means here (see also plan_limits.py for usage caps):
  - api_keys:     long-lived programmatic credentials, scoped to one
                  organization, read-only or read-write
  - ip_allowlist: restrict an organization's workspace traffic to
                  approved networks (CIDR ranges)
  - audit_export: export the organization-wide audit trail as CSV/JSON

Features are gated by plan through FEATURE_MATRIX, same "vocabulary in
code, not a migration" approach as PLAN_LIMITS.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import ipaddress
import json
import secrets
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Feature gating
# ---------------------------------------------------------------------------

FEATURE_API_KEYS = "api_keys"
FEATURE_IP_ALLOWLIST = "ip_allowlist"
FEATURE_AUDIT_EXPORT = "audit_export"

FEATURE_MATRIX: dict[str, frozenset[str]] = {
    "free": frozenset(),
    "pro": frozenset(),
    "enterprise": frozenset({FEATURE_API_KEYS, FEATURE_IP_ALLOWLIST, FEATURE_AUDIT_EXPORT}),
}


def plan_has_feature(plan: str, feature: str) -> bool:
    """Unknown/legacy plan strings get no enterprise features — the safe
    default is the most restrictive, same as plan_limits.get_plan_limits."""
    return feature in FEATURE_MATRIX.get(plan, frozenset())


def features_for_plan(plan: str) -> dict[str, bool]:
    every = {FEATURE_API_KEYS, FEATURE_IP_ALLOWLIST, FEATURE_AUDIT_EXPORT}
    return {name: plan_has_feature(plan, name) for name in sorted(every)}


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
# Format:  aida_<prefix>_<secret>
#   prefix: 8 hex chars, stored in the clear and uniquely indexed — used to
#           find the row, and shown in the UI so a key can be recognised.
#   secret: ~43 url-safe chars (256 bits). Never stored; only the SHA-256 of
#           the WHOLE key is. SHA-256 (rather than bcrypt/argon2) is the
#           right tool here because the input is 256 bits of randomness,
#           not a human-chosen password: there is nothing for a slow hash
#           to protect against brute force, and API auth runs on every
#           request.

API_KEY_PREFIX = "aida"
KEY_SCOPE_READ = "read"
KEY_SCOPE_READ_WRITE = "read_write"
VALID_KEY_SCOPES = frozenset({KEY_SCOPE_READ, KEY_SCOPE_READ_WRITE})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

DEFAULT_KEY_TTL_DAYS = 90
MAX_KEY_TTL_DAYS = 365
LAST_USED_TOUCH_INTERVAL = timedelta(minutes=5)


def looks_like_api_key(token: str) -> bool:
    """Cheap routing check used by get_current_user to decide between the
    API-key path and the JWT path. Not a validity check."""
    return token.startswith(API_KEY_PREFIX + "_")


def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, key_hash). The full key is shown to the
    user exactly once; only prefix and key_hash are persisted."""
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    full_key = f"{API_KEY_PREFIX}_{prefix}_{secret}"
    return full_key, prefix, hash_api_key(full_key)


def hash_api_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


def parse_api_key_prefix(token: str) -> str | None:
    """Extracts the lookup prefix, or None if the token is malformed."""
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != API_KEY_PREFIX:
        return None
    prefix, secret = parts[1], parts[2]
    if len(prefix) != 8 or not all(c in "0123456789abcdef" for c in prefix):
        return None
    if len(secret) < 20:
        return None
    return prefix


def verify_api_key(presented_key: str, stored_hash: str) -> bool:
    """Constant-time comparison, so response timing can't leak how many
    leading characters of a guessed key's hash were right."""
    return hmac.compare_digest(hash_api_key(presented_key), stored_hash)


def _as_utc(value: datetime | None) -> datetime | None:
    """Postgres returns timezone-aware datetimes for timestamptz columns,
    but SQLite (used by the test fixtures) drops tzinfo. Treat a naive
    value as UTC — that is what this codebase always stores — so
    comparisons against an aware `now` work on both instead of raising
    TypeError."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def key_unusable_reason(
    *, revoked_at: datetime | None, expires_at: datetime | None, now: datetime
) -> str | None:
    """None if the key may be used, else a short machine-readable reason.
    (Callers return one generic 401 to the client regardless — the reason
    is for logs, not for telling an attacker which check failed.)"""
    expires_at = _as_utc(expires_at)
    if revoked_at is not None:
        return "revoked"
    if expires_at is not None and now >= expires_at:
        return "expired"
    return None


def scope_allows(scope: str, http_method: str) -> bool:
    """A read key may only make safe (read-only) requests; read_write may
    make any. Unknown scope values allow nothing."""
    if scope == KEY_SCOPE_READ_WRITE:
        return True
    if scope == KEY_SCOPE_READ:
        return http_method.upper() in SAFE_METHODS
    return False


def compute_expiry(now: datetime, ttl_days: int | None) -> datetime:
    """ttl_days=None means the default (90 days) — enterprise keys always
    expire; there is deliberately no "never expires" option."""
    days = DEFAULT_KEY_TTL_DAYS if ttl_days is None else ttl_days
    if days < 1 or days > MAX_KEY_TTL_DAYS:
        raise ValueError(f"ttl_days must be between 1 and {MAX_KEY_TTL_DAYS}")
    return now + timedelta(days=days)


def should_touch_last_used(last_used_at: datetime | None, now: datetime) -> bool:
    """Throttles the last_used_at write to once per interval, so a busy
    integration doesn't turn every read request into a DB write."""
    last_used_at = _as_utc(last_used_at)
    return last_used_at is None or now - last_used_at >= LAST_USED_TOUCH_INTERVAL


# ---------------------------------------------------------------------------
# IP allowlist
# ---------------------------------------------------------------------------

MAX_ALLOWLIST_ENTRIES = 100


class InvalidAllowlistError(ValueError):
    pass


def _normalize_ip(value: str):
    """Parses an IP, unwrapping IPv4-mapped IPv6 (::ffff:1.2.3.4) so a
    dual-stack proxy can't make an allowed IPv4 client look like a
    different, non-matching IPv6 address."""
    ip = ipaddress.ip_address(value.strip())
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def normalize_allowlist(entries: list[str]) -> list[str]:
    """Validates and canonicalises CIDR entries (a bare IP becomes /32 or
    /128). De-duplicates, preserving first-seen order. Raises
    InvalidAllowlistError with a message naming the bad entry."""
    if len(entries) > MAX_ALLOWLIST_ENTRIES:
        raise InvalidAllowlistError(f"at most {MAX_ALLOWLIST_ENTRIES} entries allowed")
    result: list[str] = []
    for raw in entries:
        text = raw.strip()
        if not text:
            raise InvalidAllowlistError("empty entry")
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise InvalidAllowlistError(f"invalid CIDR/IP: {raw!r}") from exc
        if network.prefixlen == 0:
            # 0.0.0.0/0 allows everyone: a no-op that looks like a control.
            raise InvalidAllowlistError(f"{raw!r} matches every address; remove the allowlist instead")
        canonical = str(network)
        if canonical not in result:
            result.append(canonical)
    return result


def ip_allowed(client_ip: str | None, allowlist: list[str]) -> bool:
    """Empty allowlist = feature off = everyone allowed. A non-empty
    allowlist FAILS CLOSED: an unknown or unparsable client address is
    denied, not waved through."""
    if not allowlist:
        return True
    if not client_ip:
        return False
    try:
        ip = _normalize_ip(client_ip)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue  # a corrupt stored entry must not crash or allow anything
        if ip.version == network.version and ip in network:
            return True
    return False


# ---------------------------------------------------------------------------
# Audit export
# ---------------------------------------------------------------------------

EXPORT_COLUMNS = [
    "created_at",
    "workspace_id",
    "workspace_name",
    "actor_user_id",
    "actor_email",
    "action",
    "target_type",
    "target_id",
    "metadata",
]
MAX_EXPORT_ROWS = 50_000
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    """Neutralises CSV/formula injection. Audit rows contain attacker-
    influenced text (invited emails, comment-derived metadata); a cell
    beginning with = + - @ is executed as a formula by Excel/Sheets when
    the export is opened. Prefixing an apostrophe makes it inert text."""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_TRIGGERS):
        return "'" + text
    return text


def rows_to_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                csv_safe(
                    json.dumps(row.get(col), sort_keys=True, default=str)
                    if col == "metadata"
                    else row.get(col)
                )
                for col in EXPORT_COLUMNS
            ]
        )
    return buffer.getvalue()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
