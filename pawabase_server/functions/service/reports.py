"""What one run of an HTTP function reports when it is over, and how that becomes a ``FunctionRun`` row.

The runtime reports; the server records. In the embedded runtime the report is handed to the server's record buffer directly; the
runtime service posts it to the server's internal listener with the invocation's grant, which is what makes the row trustworthy (the
grant names the environment, the function and the request; the service cannot record runs for anything else).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from pawabase_server.core.ids import new_ulid
from pawabase_server.db.models import FunctionRun

#: Lines of ``console.*`` kept per run; the rest is counted.
MAX_LOG_LINES = 500
MAX_LINE = 4000


@dataclass
class RunReport:
    invocation: str
    env: str
    branch: str
    function: str
    deployment_id: str | None
    method: str
    path: str
    request_id: str | None
    #: ``succeeded`` (any response below 500), ``failed`` (5xx, a crash, a timeout) or ``cancelled`` (the caller went away first).
    status: str
    status_code: int | None
    duration_ms: float
    cold_start: bool = False
    worker: str | None = None
    error: str | None = None
    logs: list[dict[str, Any]] = field(default_factory=list)
    request_bytes: int = 0
    response_bytes: int = 0
    background_ms: float | None = None
    fetches: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunReport:
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in known})

    def trimmed_logs(self) -> list[dict[str, Any]]:
        lines = [
            {**line, "message": str(line.get("message", ""))[:MAX_LINE]} for line in self.logs[:MAX_LOG_LINES]
        ]
        if len(self.logs) > MAX_LOG_LINES:
            lines.append({"level": "warning", "message": f"… {len(self.logs) - MAX_LOG_LINES} more lines were not kept"})
        return lines

    def to_record(self) -> FunctionRun:
        return FunctionRun(
            id=new_ulid(),
            env=self.env,
            branch=self.branch,
            function=self.function,
            deployment_id=self.deployment_id,
            trigger="http",
            status=self.status,
            input={"method": self.method, "path": self.path},
            output={"status": self.status_code} if self.status_code is not None else {},
            error=self.error,
            logs=self.trimmed_logs(),
            duration_ms=round(self.duration_ms, 3),
            request_id=self.request_id,
            kind="http",
            method=self.method,
            path=self.path[:512],
            status_code=self.status_code,
            cold_start=self.cold_start,
            worker=self.worker,
            request_bytes=self.request_bytes,
            response_bytes=self.response_bytes,
            background_ms=self.background_ms,
            fetches=self.fetches,
        )
