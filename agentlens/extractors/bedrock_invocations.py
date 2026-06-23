"""``ext-bedrock-invocations``. CloudWatch Logs ModelInvocationLog (API-pull)."""

from __future__ import annotations

import os
from collections.abc import Iterator

from extractors.base import BaseExtractor, Page
from extractors.core.aws_client import AwsClientSource
from schemas.bedrock_invocations import RawBedrockInvocation


class BedrockInvocationsExtractor(AwsClientSource, BaseExtractor):
    name = "ext-bedrock-invocations"
    schema = RawBedrockInvocation
    source_path = "bedrock/invocation_logs"
    source_id_field = "request_id"
    timestamp_field = "timestamp"
    agent_id_field = "identity_arn"

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        client = self._aws_client("logs")
        log_group = os.environ.get(
            "BEDROCK_INVOCATION_LOG_GROUP", "/aws/bedrock/modelinvocations"
        )
        token: str | None = None
        while True:
            kwargs = {"logGroupName": log_group, "limit": 1000}
            if token:
                kwargs["nextToken"] = token
            self.rate_limit().before_request()
            resp = client.filter_log_events(**kwargs)
            yield [
                {"request_id": e.get("eventId"), **e} for e in resp.get("events", [])
            ]
            token = resp.get("nextToken")
            if not token:
                break
