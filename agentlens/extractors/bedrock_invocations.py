"""``ext-bedrock-invocations``. CloudWatch Logs ModelInvocationLog (API-pull)."""

from __future__ import annotations

import json
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
            yield [self._map_event(e) for e in resp.get("events", [])]
            token = resp.get("nextToken")
            if not token:
                break

    @staticmethod
    def _map_event(event: dict) -> dict:
        """Sobre de CloudWatch -> registro plano ModelInvocationLog (seam live).

        El evento de filter_log_events trae el ModelInvocationLog como string
        JSON en ``message``; el schema espera los campos ya aplanados (la forma
        de las fixtures). Con entrega de solo metadatos, input/output llegan
        sin payload pero con los token counts.
        """
        try:
            rec = json.loads(event.get("message") or "{}")
        except ValueError:
            rec = {}
        return {
            "request_id": rec.get("requestId") or event.get("eventId"),
            "timestamp": rec.get("timestamp"),
            "schema_type": rec.get("schemaVersion"),
            "account_id": rec.get("accountId"),
            "identity_arn": (rec.get("identity") or {}).get("arn"),
            "region": rec.get("region"),
            "operation": rec.get("operation"),
            "model_id": rec.get("modelId"),
            "input_token_count": (rec.get("input") or {}).get("inputTokenCount"),
            "output_token_count": (rec.get("output") or {}).get("outputTokenCount"),
        }
