"""Pawabase queue jobs. Imported here so a worker can resolve every payload."""

from pawabase_server.automation.jobs.environments import PurgeEnvironmentJob
from pawabase_server.automation.jobs.events import ProcessEventJob
from pawabase_server.automation.jobs.flows import RunFlowJob
from pawabase_server.automation.jobs.functions import RunFunctionJob
from pawabase_server.automation.jobs.mail import SendMailJob
from pawabase_server.automation.jobs.webhooks import DeliverWebhookJob

__all__ = [
    "DeliverWebhookJob",
    "ProcessEventJob",
    "PurgeEnvironmentJob",
    "RunFlowJob",
    "RunFunctionJob",
    "SendMailJob",
]
