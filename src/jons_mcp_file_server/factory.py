"""Factory function for file server selection."""

import os
from typing import Literal

from .protocol import FileServer
from .gcs import GCSFileServer
from .localhost import LocalhostFileServer

_instance: FileServer | None = None


def get_file_server(
    backend: Literal["gcs", "localhost"] = "localhost",
    **kwargs,
) -> FileServer:
    """Get or create the file server instance.

    This function implements a singleton pattern - the first call creates the
    server instance, and subsequent calls return the same instance.

    Args:
        backend: Which backend to use
            - "gcs": Use GCS signed URLs (requires GCS_BUCKET env var or bucket_name kwarg)
            - "localhost": Use LocalhostFileServer (localhost URLs only)
        **kwargs: Backend-specific configuration
            For gcs:
                - bucket_name: GCS bucket name (or use GCS_BUCKET env var)
                - download_ttl: Seconds until download URLs expire (default 900)
                - upload_ttl: Seconds until upload URLs expire (default 300)
                - credentials_path: Path to service account JSON (or use GOOGLE_APPLICATION_CREDENTIALS)
            For localhost:
                - port: Port to listen on (default 9171 or MCP_FILE_SERVER_PORT env var)
                - download_token_ttl: Seconds until download tokens expire (default 3600)
                - upload_token_ttl: Seconds until upload tokens expire (default 300)

    Returns:
        The FileServer instance.

    Raises:
        ValueError: If GCS backend is selected but no bucket is configured.
    """
    global _instance

    if _instance is not None:
        return _instance

    if backend == "gcs":
        bucket_name = kwargs.get("bucket_name") or os.environ.get("GCS_BUCKET")
        if not bucket_name:
            raise ValueError(
                "GCS_BUCKET environment variable or bucket_name parameter required for GCS backend"
            )

        _instance = GCSFileServer(
            bucket_name=bucket_name,
            download_ttl=kwargs.get("download_ttl", 900),
            upload_ttl=kwargs.get("upload_ttl", 300),
            credentials_path=kwargs.get("credentials_path")
            or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        )
    else:
        _instance = LocalhostFileServer(
            port=kwargs.get("port"),
            download_token_ttl=kwargs.get("download_token_ttl", 3600),
            upload_token_ttl=kwargs.get("upload_token_ttl", 300),
        )

    return _instance


def cleanup_file_server() -> None:
    """Stop and clean up the file server instance.

    Call this at shutdown to release resources (temp files, etc.)
    """
    global _instance
    if _instance is not None:
        _instance.stop()
        _instance = None


def reset_file_server() -> None:
    """Reset the file server singleton (for testing).

    This allows creating a new server instance with different configuration.
    Does not stop the existing server - call cleanup_file_server() first if needed.
    """
    global _instance
    _instance = None
