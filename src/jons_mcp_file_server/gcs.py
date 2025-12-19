"""GCS file server using signed URLs."""

import logging
import os
import shutil
import tempfile
import uuid
from datetime import timedelta

from google.cloud import storage

logger = logging.getLogger(__name__)


class GCSFileServer:
    """File server using Google Cloud Storage signed URLs.

    Provides signed GET URLs for downloads and signed PUT URLs for uploads.
    Files are uploaded to GCS and signed URLs allow direct access without
    requiring a running server.
    """

    def __init__(
        self,
        bucket_name: str,
        download_ttl: int = 900,  # 15 minutes
        upload_ttl: int = 300,  # 5 minutes
        credentials_path: str | None = None,
    ):
        """Initialize the GCS file server.

        Args:
            bucket_name: Name of the GCS bucket to use.
            download_ttl: Seconds until download URLs expire.
            upload_ttl: Seconds until upload URLs expire.
            credentials_path: Path to service account JSON file. If None,
                uses Application Default Credentials.
        """
        self._bucket_name = bucket_name
        self._download_ttl = download_ttl
        self._upload_ttl = upload_ttl
        self._credentials_path = credentials_path
        self._client: storage.Client | None = None
        self._bucket: storage.Bucket | None = None
        self._pending_uploads: dict[str, dict] = {}
        self._temp_dir = tempfile.mkdtemp(prefix="mcp-gcs-")

    def _ensure_client(self) -> None:
        """Lazily initialize the GCS client."""
        if self._client is None:
            if self._credentials_path:
                self._client = storage.Client.from_service_account_json(
                    self._credentials_path
                )
            else:
                # Use Application Default Credentials
                self._client = storage.Client()
            self._bucket = self._client.bucket(self._bucket_name)
            logger.info(f"Initialized GCS client for bucket: {self._bucket_name}")

    def register_download(self, local_path: str, filename: str) -> dict:
        """Upload file to GCS and return signed GET URL.

        Args:
            local_path: Path to the local file to upload.
            filename: The filename to use when downloading.

        Returns:
            Dictionary with url, curl command, and token for cleanup.
        """
        self._ensure_client()

        # Generate unique GCS path
        gcs_path = f"downloads/{uuid.uuid4().hex}/{filename}"
        blob = self._bucket.blob(gcs_path)

        # Upload the file
        blob.upload_from_filename(local_path)
        logger.debug(f"Uploaded {local_path} to gs://{self._bucket_name}/{gcs_path}")

        # Generate signed URL
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=self._download_ttl),
            method="GET",
        )

        return {
            "url": url,
            "curl": f"curl -o '{filename}' '{url}'",
            "token": gcs_path,
        }

    def register_upload(
        self,
        filename: str | None = None,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> dict:
        """Generate signed PUT URL for direct upload to GCS.

        Args:
            filename: Optional suggested filename for the upload.
            max_bytes: Maximum allowed upload size (for documentation only,
                GCS doesn't enforce this in signed URLs).

        Returns:
            Dictionary with uploadUrl, uploadToken, method, expiresIn, and curl.
        """
        self._ensure_client()

        token = uuid.uuid4().hex
        safe_filename = filename or "file"
        gcs_path = f"uploads/{token}/{safe_filename}"

        blob = self._bucket.blob(gcs_path)
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=self._upload_ttl),
            method="PUT",
        )

        # Track pending upload
        self._pending_uploads[token] = {
            "gcs_path": gcs_path,
            "filename": safe_filename,
            "max_bytes": max_bytes,
        }

        return {
            "uploadUrl": url,
            "uploadToken": token,
            "method": "PUT",  # GCS uses PUT, not POST
            "expiresIn": self._upload_ttl,
            "curl": f"curl -X PUT -H 'Content-Type: application/octet-stream' -T '{safe_filename}' '{url}'",
        }

    def resolve_upload(self, token: str) -> dict:
        """Download from GCS to local temp and return path.

        Args:
            token: The upload token from register_upload.

        Returns:
            Dictionary with local_path and filename.

        Raises:
            ValueError: If the token is invalid.
        """
        if token not in self._pending_uploads:
            raise ValueError(f"Invalid upload token: {token}")

        self._ensure_client()

        info = self._pending_uploads[token]
        gcs_path = info["gcs_path"]
        filename = info["filename"]

        # Download to local temp
        local_path = os.path.join(self._temp_dir, f"{token}-{filename}")
        blob = self._bucket.blob(gcs_path)

        try:
            blob.download_to_filename(local_path)
            logger.debug(f"Downloaded gs://{self._bucket_name}/{gcs_path} to {local_path}")
        except Exception as e:
            raise ValueError(f"Failed to download uploaded file: {e}") from e

        return {
            "local_path": local_path,
            "filename": filename,
        }

    def consume_upload(self, token: str) -> None:
        """Mark upload token as consumed and cleanup.

        Args:
            token: The upload token to consume.
        """
        self._pending_uploads.pop(token, None)

    @property
    def is_running(self) -> bool:
        """GCS is always 'running' - no server to start."""
        return True

    def ensure_running(self) -> None:
        """Initialize the GCS client if needed."""
        self._ensure_client()

    def stop(self) -> None:
        """Clean up temp files."""
        if os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            logger.debug(f"Cleaned up temp directory: {self._temp_dir}")
