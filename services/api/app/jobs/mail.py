"""Sending email as a background job, so SMTP latency never holds a request."""

from __future__ import annotations

from typing import Any

from app.jobs.base import PawabaseJob


class SendMailJob(PawabaseJob):
    queue = "mail"
    tries = 3
    backoff = 5

    async def perform(self) -> Any:
        platform, state = await self.environment()
        return await platform.mail.send(
            state,
            list(self.params["to"]),
            self.params.get("subject") or "",
            text=self.params.get("text"),
            html=self.params.get("html"),
            template=self.params.get("template"),
            data=self.params.get("data") or {},
            source=self.params.get("source", "api"),
        )
