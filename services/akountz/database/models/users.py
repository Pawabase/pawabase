"""Users and everything attached to them.

:class:`AuthUser` is Sillo's :class:`~sillo.users.base.UserBaseModel` (password
hashing, verification stamps, ``identity``) with Sillo's
:class:`~sillo.auth.jwt_auth.JWTUserMixin` (token families, rotation, theft
detection, revocation). Pawabase adds tenancy: every user belongs to one
project environment, so an address may sign up once per environment rather
than once per installation.
"""

from __future__ import annotations

from sillo.auth.jwt_auth import JWTUserMixin
from sillo.permissions import PermissionMixin
from sillo.record import Model
from sillo.users.base import UserBaseModel
from tortoise import fields

from database.fields import AnyJSONField


class AuthUser(PermissionMixin, JWTUserMixin, UserBaseModel):
    """A project environment's user.

    Attributes:
        project, env: The environment the account belongs to.
        email, username: Unique per environment (Sillo's base makes them
            globally unique; these redefinitions scope them).
        user_metadata: Profile data the user may edit.
        app_metadata: Data only the application (service keys, Studio) may edit.
        disabled_at: Set when an administrator disables the account.
        failed_logins, locked_until: Brute-force lockout.
    """

    project = fields.CharField(max_length=63, db_index=True)
    env = fields.CharField(max_length=63)
    email = fields.CharField(max_length=255, db_index=True)
    username = fields.CharField(max_length=150, db_index=True)
    name = fields.CharField(max_length=200, default="")
    avatar_url = fields.CharField(max_length=2048, null=True)
    phone = fields.CharField(max_length=32, null=True)
    user_metadata = AnyJSONField(default=dict)
    app_metadata = AnyJSONField(default=dict)
    disabled_at = fields.DatetimeField(null=True)
    failed_logins = fields.IntField(default=0)
    locked_until = fields.DatetimeField(null=True)
    mfa_enabled = fields.BooleanField(default=False)
    last_sign_in_ip = fields.CharField(max_length=64, null=True)

    class Meta:
        table = "akz_users"
        unique_together = (("project", "env", "email"), ("project", "env", "username"))

    @property
    def is_disabled(self) -> bool:
        return self.disabled_at is not None or not self.is_active


class Identity(Model):
    """An external identity (OAuth/OIDC) linked to a user."""

    id = fields.IntField(primary_key=True)
    user = fields.ForeignKeyField(
        "models.AuthUser", related_name="identities", on_delete=fields.CASCADE
    )
    project = fields.CharField(max_length=63)
    env = fields.CharField(max_length=63)
    provider = fields.CharField(max_length=64)
    subject = fields.CharField(max_length=255)
    email = fields.CharField(max_length=255, null=True)
    email_verified = fields.BooleanField(default=False)
    data = AnyJSONField(default=dict)
    last_sign_in_at = fields.DatetimeField(null=True)

    class Meta:
        table = "akz_identities"
        unique_together = (("project", "env", "provider", "subject"),)


class MfaFactor(Model):
    """A second factor. TOTP secrets are encrypted at rest."""

    id = fields.IntField(primary_key=True)
    user = fields.ForeignKeyField(
        "models.AuthUser", related_name="factors", on_delete=fields.CASCADE
    )
    kind = fields.CharField(max_length=16, default="totp")
    secret_ciphertext = fields.TextField()
    confirmed_at = fields.DatetimeField(null=True)
    last_used_step = fields.BigIntField(default=0)
    friendly_name = fields.CharField(max_length=100, default="Authenticator app")

    class Meta:
        table = "akz_mfa_factors"


class RecoveryCode(Model):
    """A single-use MFA recovery code, stored as a SHA-256 hash."""

    id = fields.IntField(primary_key=True)
    user = fields.ForeignKeyField(
        "models.AuthUser", related_name="recovery_codes", on_delete=fields.CASCADE
    )
    code_hash = fields.CharField(max_length=128, db_index=True)
    used_at = fields.DatetimeField(null=True)

    class Meta:
        table = "akz_recovery_codes"


class OneTimeToken(Model):
    """A single-use token for verification, recovery, magic links, invitations and MFA challenges.

    The token itself is signed with Sillo's ``URLSafeTimedSerializer`` and
    carries this row's id; the row makes it single-use.
    """

    id = fields.CharField(max_length=32, primary_key=True)
    project = fields.CharField(max_length=63, db_index=True)
    env = fields.CharField(max_length=63)
    purpose = fields.CharField(max_length=32)
    user = fields.ForeignKeyField(
        "models.AuthUser", related_name="tokens", null=True, on_delete=fields.CASCADE
    )
    email = fields.CharField(max_length=255, null=True)
    data = AnyJSONField(default=dict)
    expires_at = fields.DatetimeField()
    used_at = fields.DatetimeField(null=True)

    class Meta:
        table = "akz_one_time_tokens"


class SessionInfo(Model):
    """What Pawabase knows about one token family (a session).

    Sillo's ``JWTToken`` rows carry the family's rotation and revocation state;
    this row adds the device, the assurance level, and how the session began.
    """

    family = fields.CharField(max_length=64, primary_key=True)
    user = fields.ForeignKeyField(
        "models.AuthUser", related_name="sessions", on_delete=fields.CASCADE
    )
    project = fields.CharField(max_length=63, db_index=True)
    env = fields.CharField(max_length=63)
    method = fields.CharField(max_length=32)
    aal = fields.CharField(max_length=8, default="aal1")
    # The organization (slug) this session's tokens are issued for, if any.
    # Refreshes keep it; a refresh or sign-in that names another org switches it.
    org = fields.CharField(max_length=63, null=True)
    ip = fields.CharField(max_length=64, null=True)
    user_agent = fields.CharField(max_length=512, null=True)
    last_refreshed_at = fields.DatetimeField(null=True)
    revoked_at = fields.DatetimeField(null=True)

    class Meta:
        table = "akz_sessions"


class LoginEvent(Model):
    """Authentication history: sign-ins, failures, refreshes, sign-outs, resets."""

    id = fields.IntField(primary_key=True)
    project = fields.CharField(max_length=63, db_index=True)
    env = fields.CharField(max_length=63)
    user_id = fields.IntField(null=True, db_index=True)
    email = fields.CharField(max_length=255, null=True)
    kind = fields.CharField(max_length=32)
    method = fields.CharField(max_length=32, default="password")
    success = fields.BooleanField(default=True)
    reason = fields.CharField(max_length=255, null=True)
    ip = fields.CharField(max_length=64, null=True)
    user_agent = fields.CharField(max_length=512, null=True)

    class Meta:
        table = "akz_login_events"
        ordering = ["-id"]
