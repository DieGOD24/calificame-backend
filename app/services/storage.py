import os
from abc import ABC, abstractmethod
from pathlib import Path

from app.config import settings


class StorageService(ABC):
    """Abstract base class for file storage."""

    @abstractmethod
    def save_file(self, file_bytes: bytes, path: str) -> str:
        """Save file bytes to storage. Returns the storage path."""
        ...

    @abstractmethod
    def get_file(self, path: str) -> bytes:
        """Retrieve file bytes from storage."""
        ...

    @abstractmethod
    def delete_file(self, path: str) -> None:
        """Delete a file from storage."""
        ...

    @abstractmethod
    def get_file_url(self, path: str) -> str:
        """Get a URL or path to access the file."""
        ...


class LocalStorageService(StorageService):
    """Store files on the local filesystem."""

    def __init__(self, base_path: str | None = None) -> None:
        self.base_path = Path(base_path or settings.STORAGE_LOCAL_PATH)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save_file(self, file_bytes: bytes, path: str) -> str:
        full_path = self.base_path / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(file_bytes)
        return str(full_path)

    def get_file(self, path: str) -> bytes:
        full_path = self.base_path / path
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return full_path.read_bytes()

    def delete_file(self, path: str) -> None:
        full_path = self.base_path / path
        if full_path.exists():
            full_path.unlink()

    def get_file_url(self, path: str) -> str:
        return str(self.base_path / path)


class MinIOStorageService(StorageService):
    """Store files in MinIO/S3-compatible storage."""

    def __init__(self) -> None:
        import boto3
        from botocore.client import Config

        self.bucket = settings.MINIO_BUCKET
        endpoint_url = f"{'https' if settings.MINIO_SECURE else 'http'}://{settings.MINIO_ENDPOINT}"

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )

        # Ensure bucket exists
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def save_file(self, file_bytes: bytes, path: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=path, Body=file_bytes)
        return path

    def get_file(self, path: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=path)
        return response["Body"].read()

    def delete_file(self, path: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=path)

    def get_file_url(self, path: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": path},
            ExpiresIn=3600,
        )


_storage_service: StorageService | None = None


def get_storage_service() -> StorageService:
    """Factory function to get the configured storage service."""
    global _storage_service  # noqa: PLW0603
    if _storage_service is None:
        if settings.STORAGE_TYPE == "minio":
            _storage_service = MinIOStorageService()
        else:
            _storage_service = LocalStorageService()
    return _storage_service


def reset_storage_service() -> None:
    """Reset the storage service singleton (useful for testing)."""
    global _storage_service  # noqa: PLW0603
    _storage_service = None


def set_storage_service(service: StorageService) -> None:
    """Override the storage service (useful for testing)."""
    global _storage_service  # noqa: PLW0603
    _storage_service = service
