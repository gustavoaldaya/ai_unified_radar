"""boto3 seam for AWS/Bedrock-backed extractors.

A mixin so extractors can ``class BedrockCostExtractor(AwsClientSource,
BaseExtractor)``. ``_aws_client`` is the seam; tests override it to return a
fake client, so pagination runs without boto3 or AWS credentials. Direct
API-pull to ADLS, no S3/CUR (ADR-008).
"""

from __future__ import annotations

import os
from typing import Any


class AwsClientSource:
    def _aws_region(self) -> str | None:
        return os.environ.get("AWS_REGION")

    def _aws_client(self, service: str) -> Any:
        import boto3

        return boto3.client(service, region_name=self._aws_region())
