"""``ext-bedrock-traces``. AgentCore OTEL spans via CloudWatch Logs (API-pull)."""

from __future__ import annotations

import os
from collections.abc import Iterator

from extractors.base import BaseExtractor, Page
from extractors.core.aws_client import AwsClientSource
from schemas.bedrock_traces import RawBedrockTrace


class BedrockTracesExtractor(AwsClientSource, BaseExtractor):
    name = "ext-bedrock-traces"
    schema = RawBedrockTrace
    source_path = "bedrock/traces"
    source_id_field = "span_id"
    timestamp_field = "timestamp"
    agent_id_field = "agent_endpoint_id"

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        client = self._aws_client("logs")
        log_group = os.environ.get(
            "BEDROCK_AGENTCORE_LOG_GROUP", "/aws/bedrock-agentcore/traces"
        )
        token: str | None = None
        while True:
            kwargs = {"logGroupName": log_group, "limit": 1000}
            if token:
                kwargs["nextToken"] = token
            self.rate_limit().before_request()
            resp = client.filter_log_events(**kwargs)
            yield [{"span_id": e.get("eventId"), **e} for e in resp.get("events", [])]
            token = resp.get("nextToken")
            if not token:
                break
