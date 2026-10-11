"""The codes a client sees: WebSocket close codes, and the ``error`` names in messages and acknowledgements."""

from __future__ import annotations

# WebSocket close codes (4000-4999 are for applications).
CLOSE_NO_KEY = 4001
CLOSE_BAD_TOKEN = 4003
CLOSE_ABUSE = 4008
CLOSE_CAPACITY = 4429
CLOSE_GOING_AWAY = 1012

# ``error`` names.
MESSAGE_TOO_LARGE = "message_too_large"
INVALID_JSON = "invalid_json"
INVALID_MESSAGE = "invalid_message"
BAD_REQUEST = "bad_request"
UNKNOWN_TYPE = "unknown_type"
FORBIDDEN = "forbidden"
UNAVAILABLE = "unavailable"
INVALID_TOKEN = "invalid_token"
RATE_LIMITED = "rate_limited"
PAYLOAD_TOO_LARGE = "payload_too_large"
CAPACITY = "capacity"
