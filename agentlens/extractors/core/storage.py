"""Storage backends.

The framework writes through a :class:`StorageBackend` so the *same* pipeline
serves fixtures (local filesystem) and live (ADLS Gen2 via Managed Identity,
no account keys -- ADR-002).

* :class:`LocalStorageBackend` -- used under ``USE_FIXTURES`` and in tests.
* :class:`ADLSStorageBackend`  -- live cutover; azure deps imported lazily so
  fixtures runs and type-checking need no Azure SDK / credentials.
"""

from __future__ import annotations

import io
import os
import tempfile
from abc import ABC, abstractmethod
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from extractors.core.config import Settings


class StorageBackend(ABC):
    @abstractmethod
    def read_text(self, rel_path: str) -> str | None:
        """Return file contents, or ``None`` if it does not exist."""

    @abstractmethod
    def write_text_atomic(self, rel_path: str, data: str) -> None:
        """Write text atomically (all-or-nothing)."""

    @abstractmethod
    def write_parquet(self, rel_path: str, table: pa.Table) -> str:
        """Write a Parquet table (Snappy) and return the written path."""


class LocalStorageBackend(StorageBackend):
    def __init__(self, root: str) -> None:
        self._root = os.path.abspath(root)

    def _abs(self, rel_path: str) -> str:
        return os.path.join(self._root, rel_path)

    def read_text(self, rel_path: str) -> str | None:
        path = self._abs(rel_path)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def write_text_atomic(self, rel_path: str, data: str) -> None:
        path = self._abs(rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)  # atomic on the same filesystem
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def write_parquet(self, rel_path: str, table: pa.Table) -> str:
        path = self._abs(rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        pq.write_table(table, tmp, compression="snappy")  # type: ignore[no-untyped-call]
        os.replace(tmp, path)
        return path


class ADLSStorageBackend(StorageBackend):
    """Live ADLS Gen2 backend (Managed Identity). Not exercised under fixtures."""

    def __init__(
        self,
        account_url: str,
        filesystem: str,
        credential: Any | None = None,
    ) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.filedatalake import DataLakeServiceClient

        cred = credential or DefaultAzureCredential()
        service = DataLakeServiceClient(account_url, credential=cred)
        self._fs = service.get_file_system_client(filesystem)

    def read_text(self, rel_path: str) -> str | None:
        file_client = self._fs.get_file_client(rel_path)
        if not file_client.exists():
            return None
        downloaded: bytes = file_client.download_file().readall()
        return downloaded.decode("utf-8")

    def write_text_atomic(self, rel_path: str, data: str) -> None:
        # Per-blob overwrite is atomic at the service level.
        self._fs.get_file_client(rel_path).upload_data(
            data.encode("utf-8"), overwrite=True
        )

    def write_parquet(self, rel_path: str, table: pa.Table) -> str:
        buffer = io.BytesIO()
        pq.write_table(table, buffer, compression="snappy")  # type: ignore[no-untyped-call]
        self._fs.get_file_client(rel_path).upload_data(
            buffer.getvalue(), overwrite=True
        )
        return rel_path


def build_backend(settings: Settings) -> StorageBackend:
    """Pick the backend for a run: ADLS when live + configured, else Local.

    Keeps fixtures runs on the local filesystem and routes the live cutover to
    ADLS Gen2 (Managed Identity) when the account is configured.
    """
    if (
        not settings.use_fixtures
        and settings.adls_account_url
        and settings.adls_filesystem
    ):
        return ADLSStorageBackend(settings.adls_account_url, settings.adls_filesystem)
    return LocalStorageBackend(settings.raw_root)
