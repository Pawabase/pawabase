"""Second factors: an authenticator app (TOTP), codes emailed to the account, and backup codes.

Sillo has no one-time-password primitive, so TOTP (RFC 6238) comes from
``pyotp``. Secrets are encrypted at rest with the platform's secret box. A code
is accepted once: the last accepted time step is recorded, so a code
observed over a shoulder cannot be replayed within its 30 seconds.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import pyotp
from sillo.exceptions import HTTPException

from pawabase_server.core.ids import new_ulid
from pawabase_server.db.models import AuthUser, Membership, MfaFactor, OneTimeToken, RecoveryCode
from pawabase_server.identity import emails as auth_emails
from pawabase_server.identity import links, rbac
from pawabase_server.identity.accounts import Accounts
from pawabase_server.identity.environment import AuthConfig

RECOVERY_CODE_COUNT = 10
EMAIL_PURPOSE = "mfa_email"


def _hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


async def enroll(accounts: Accounts, user: AuthUser, issuer: str) -> dict[str, str]:
    """Start (or restart) authenticator-app enrolment. The factor is unconfirmed until a code is verified."""
    await MfaFactor.filter(user=user, confirmed_at=None).delete()
    secret = pyotp.random_base32()
    factor = await MfaFactor.create(
        user=user, kind="totp", secret_ciphertext=accounts.box.seal(secret)
    )
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=issuer)
    return {"factor_id": str(factor.id), "secret": secret, "otpauth_uri": uri}


async def _check(accounts: Accounts, factor: MfaFactor, code: str) -> bool:
    totp = pyotp.TOTP(accounts.box.open(factor.secret_ciphertext))
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False
    now = datetime.now(UTC).timestamp()
    for drift in (-1, 0, 1):
        step = int(now // 30) + drift
        if step <= factor.last_used_step:
            continue
        if secrets.compare_digest(totp.at(step * 30), code):
            factor.last_used_step = step
            await factor.save(update_fields=["last_used_step"])
            return True
    return False


# ── email codes ──────────────────────────────────────────────────────────


def _email_hash(user: AuthUser, code: str) -> str:
    return hashlib.sha256(f"{user.id}:{code.strip()}".encode()).hexdigest()


async def send_email_code(accounts: Accounts, config: AuthConfig, user: AuthUser) -> None:
    """Email the person a six-digit code that works once. A new code replaces any earlier one."""
    minutes = config.policy("mfa")["email_code_minutes"]
    await OneTimeToken.filter(user=user, purpose=EMAIL_PURPOSE, used_at=None).update(
        used_at=datetime.now(UTC)
    )
    code = f"{secrets.randbelow(10**6):06d}"
    await OneTimeToken.create(
        id=new_ulid(),
        env=config.env,
        purpose=EMAIL_PURPOSE,
        user=user,
        email=user.email,
        data={"hash": _email_hash(user, code)},
        expires_at=datetime.now(UTC) + timedelta(minutes=minutes),
    )
    await auth_emails.send(accounts, config, "mfa_code", user.email, code=code, minutes=minutes)


async def _check_email(user: AuthUser, code: str) -> bool:
    code = (code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        return False
    wanted = _email_hash(user, code)
    row = (
        await OneTimeToken.filter(
            user=user, purpose=EMAIL_PURPOSE, used_at=None, expires_at__gt=datetime.now(UTC)
        )
        .order_by("-id")
        .first()
    )
    if row is None or not secrets.compare_digest((row.data or {}).get("hash", ""), wanted):
        return False
    return bool(
        await OneTimeToken.filter(id=row.id, used_at=None).update(used_at=datetime.now(UTC))
    )


async def enroll_email(accounts: Accounts, config: AuthConfig, user: AuthUser) -> None:
    """Start enrolling email codes: a code goes to the account's address and the factor waits for it."""
    await MfaFactor.filter(user=user, kind="email", confirmed_at=None).delete()
    await MfaFactor.create(user=user, kind="email", secret_ciphertext="")
    await send_email_code(accounts, config, user)


# ── enrolment and verification ───────────────────────────────────────────


async def confirm(
    accounts: Accounts, user: AuthUser, code: str, *, backup_codes: int = RECOVERY_CODE_COUNT
) -> list[str] | None:
    """Confirm the pending enrolment (authenticator app or email) with its first code.

    Returns the backup codes made for the person (empty when they already had some, or backup codes are off), or None for a wrong code.
    """
    factor = await MfaFactor.filter(user=user, confirmed_at=None).order_by("-id").first()
    if factor is None:
        return None
    ok = (
        await _check_email(user, code)
        if factor.kind == "email"
        else await _check(accounts, factor, code)
    )
    if not ok:
        return None
    await MfaFactor.filter(user=user, kind=factor.kind).exclude(id=factor.id).delete()
    await MfaFactor.filter(user=user, confirmed_at=None).exclude(id=factor.id).delete()
    factor.confirmed_at = datetime.now(UTC)
    await factor.save(update_fields=["confirmed_at"])
    user.mfa_enabled = True
    await user.save(update_fields=["mfa_enabled"])
    if backup_codes and await remaining_recovery_codes(user) == 0:
        return await regenerate_recovery_codes(user, backup_codes)
    return []


def _admin_like(roles: list[str]) -> bool:
    return any(r in ("admin", "owner", "superadmin") for r in roles)


async def setup_required(config: AuthConfig, user: AuthUser) -> bool:
    """Whether the directory's policy says this person must enrol a second factor before they get a session."""
    policy = config.policy("mfa")
    if not config.mfa_enabled or policy["require"] == "off" or user.mfa_enabled:
        return False
    if not (policy["totp"] or policy["email"]):
        return False  # nothing to enrol in: requiring it would lock everyone out
    grace = policy["grace_days"]
    if grace and user.created_at and user.created_at + timedelta(days=grace) > datetime.now(UTC):
        return False
    if policy["require"] == "all":
        return True
    if user.is_staff or user.is_superuser or _admin_like(await rbac.roles_of(user)):
        return True
    return await Membership.filter(user=user, role__in=["owner", "admin"]).exists()


def methods_offered(config: AuthConfig) -> list[str]:
    policy = config.policy("mfa")
    return [m for m in ("totp", "email") if policy[m]]


async def begin(accounts: Accounts, config: AuthConfig, user: AuthUser, method: str) -> dict:
    """Start enrolling *method*; what to show the person comes back."""
    if method not in methods_offered(config):
        raise ValueError("that method is not available")
    if method == "email":
        await enroll_email(accounts, config, user)
        name, _, domain = user.email.partition("@")
        return {"method": "email", "sent_to": f"{name[:1]}***@{domain}"}
    return {"method": "totp", **await enroll(accounts, user, config.project_name or "Pawabase")}


async def setup_begin(
    accounts: Accounts, config: AuthConfig, token: str, method: str
) -> tuple[AuthUser, dict]:
    """Start the enrolment a sign-in is waiting on. ``token`` is the setup token the sign-in answered with."""
    row = await links.consume(accounts, config, "mfa_setup", token, peek=True)
    user = row.user
    if user is None or user.is_disabled:
        raise HTTPException(status_code=400, detail="the link is invalid or has expired")
    try:
        return user, await begin(accounts, config, user, method)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def setup_finish(
    accounts: Accounts, config: AuthConfig, token: str, code: str
) -> tuple[AuthUser, dict, list[str]]:
    """Confirm the pending enrolment with its first code and spend the setup token. Returns the person, the token's data and the backup codes."""
    row = await links.consume(accounts, config, "mfa_setup", token, peek=True)
    user = row.user
    if user is None or user.is_disabled:
        raise HTTPException(status_code=400, detail="the link is invalid or has expired")
    policy = config.policy("mfa")
    codes = await confirm(
        accounts,
        user,
        code,
        backup_codes=policy["backup_code_count"] if policy["backup_codes"] else 0,
    )
    if codes is None:
        raise HTTPException(status_code=400, detail="the code is not valid")
    await links.consume(accounts, config, "mfa_setup", token)
    return user, row.data or {}, codes


async def challenge_payload(
    accounts: Accounts, config: AuthConfig, user: AuthUser, *, data: dict
) -> dict:
    """The answer to a first factor when a second is needed: a challenge token and the strategies that can answer it.

    A person whose only strategy is emailed codes is sent one straight away; with an authenticator app as well, they may ask for one.
    """
    token = await links.issue(accounts, config, "mfa", user=user, data=data)
    have = await kinds(user)
    if "email" in have and "totp" not in have:
        await send_email_code(accounts, config, user)
    return {"mfa_required": True, "mfa_token": token, "factors": have}


async def kinds(user: AuthUser) -> list[str]:
    """The strategies a person has confirmed: ``totp``, ``email``, and ``recovery_code`` while backup codes remain."""
    found = list(
        dict.fromkeys(
            await MfaFactor.filter(user=user)
            .exclude(confirmed_at=None)
            .values_list("kind", flat=True)
        )
    )
    if found and await remaining_recovery_codes(user):
        found.append("recovery_code")
    return found


async def verify(accounts: Accounts, user: AuthUser, code: str) -> str | None:
    """Check an authenticator code, an emailed code or a backup code. Returns which one worked."""
    factor = await MfaFactor.filter(user=user, kind="totp").exclude(confirmed_at=None).first()
    if factor is not None and await _check(accounts, factor, code):
        return "totp"
    if await MfaFactor.filter(user=user, kind="email").exclude(
        confirmed_at=None
    ).exists() and await _check_email(user, code):
        return "email"
    recovery = await RecoveryCode.filter(user=user, code_hash=_hash(code), used_at=None).first()
    if recovery is not None:
        updated = await RecoveryCode.filter(id=recovery.id, used_at=None).update(
            used_at=datetime.now(UTC)
        )
        if updated:
            return "recovery_code"
    return None


async def disable(user: AuthUser) -> None:
    await MfaFactor.filter(user=user).delete()
    await RecoveryCode.filter(user=user).delete()
    user.mfa_enabled = False
    await user.save(update_fields=["mfa_enabled"])


async def regenerate_recovery_codes(user: AuthUser, count: int = RECOVERY_CODE_COUNT) -> list[str]:
    await RecoveryCode.filter(user=user).delete()
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(5)
        code = f"{raw[:5]}-{raw[5:]}"
        codes.append(code)
        await RecoveryCode.create(user=user, code_hash=_hash(code))
    return codes


async def remaining_recovery_codes(user: AuthUser) -> int:
    return await RecoveryCode.filter(user=user, used_at=None).count()
