"""TOTP second factors and recovery codes.

Sillo has no one-time-password primitive, so TOTP (RFC 6238) comes from
``pyotp``. Secrets are encrypted at rest with the platform's secret box. A code
is accepted once: the last accepted time step is recorded, so a code
observed over a shoulder cannot be replayed within its 30 seconds.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

import pyotp

from app.platform import Akountz
from database.models import AuthUser, MfaFactor, RecoveryCode

RECOVERY_CODE_COUNT = 10


def _hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


async def enroll(akountz: Akountz, user: AuthUser, issuer: str) -> dict[str, str]:
    """Start (or restart) TOTP enrolment. The factor is unconfirmed until a code is verified."""
    await MfaFactor.filter(user=user, confirmed_at=None).delete()
    secret = pyotp.random_base32()
    factor = await MfaFactor.create(
        user=user, kind="totp", secret_ciphertext=akountz.box.seal(secret)
    )
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=issuer)
    return {"factor_id": str(factor.id), "secret": secret, "otpauth_uri": uri}


async def _check(akountz: Akountz, factor: MfaFactor, code: str) -> bool:
    totp = pyotp.TOTP(akountz.box.open(factor.secret_ciphertext))
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


async def confirm(akountz: Akountz, user: AuthUser, code: str) -> list[str] | None:
    """Confirm enrolment with a first code. Returns fresh recovery codes."""
    factor = await MfaFactor.filter(user=user, confirmed_at=None).order_by("-id").first()
    if factor is None or not await _check(akountz, factor, code):
        return None
    await MfaFactor.filter(user=user).exclude(id=factor.id).delete()
    factor.confirmed_at = datetime.now(UTC)
    await factor.save(update_fields=["confirmed_at"])
    user.mfa_enabled = True
    await user.save(update_fields=["mfa_enabled"])
    return await regenerate_recovery_codes(user)


async def verify(akountz: Akountz, user: AuthUser, code: str) -> str | None:
    """Check a TOTP code or a recovery code. Returns which one worked."""
    factor = await MfaFactor.filter(user=user).exclude(confirmed_at=None).first()
    if factor is not None and await _check(akountz, factor, code):
        return "totp"
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


async def regenerate_recovery_codes(user: AuthUser) -> list[str]:
    await RecoveryCode.filter(user=user).delete()
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(5)
        code = f"{raw[:5]}-{raw[5:]}"
        codes.append(code)
        await RecoveryCode.create(user=user, code_hash=_hash(code))
    return codes


async def remaining_recovery_codes(user: AuthUser) -> int:
    return await RecoveryCode.filter(user=user, used_at=None).count()
