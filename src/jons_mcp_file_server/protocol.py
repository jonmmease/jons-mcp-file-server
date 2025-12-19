"""FileServer protocol definition."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class FileServer(Protocol):
    """Protocol for file servers supporting upload/download via URLs.

    Implementations can use different backends (GCS signed URLs, localhost, etc.)
    while providing a consistent interface for MCP tools.
    """

    def register_download(self, local_path: str, filename: str) -> dict:
        """Register a local file for download.

        Uploads the file to the backend and returns a URL for downloading.

        Args:
            local_path: Path to the local file to make available for download.
            filename: The filename to use when downloading.

        Returns:
            Dictionary containing:
                - url: The download URL
                - curl: A curl command to download the file
                - token: An identifier for cleanup (backend-specific)
        """
        ...

    def register_upload(
        self,
        filename: str | None = None,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> dict:
        """Register an upload endpoint.

        Creates a URL where a client can upload a file directly.

        Args:
            filename: Optional suggested filename for the upload.
            max_bytes: Maximum allowed upload size in bytes.

        Returns:
            Dictionary containing:
                - uploadUrl: The URL to upload to
                - uploadToken: Token to resolve the upload later
                - method: HTTP method to use (PUT or POST)
                - expiresIn: Seconds until the upload URL expires
                - curl: A curl command example for uploading
        """
        ...

    def resolve_upload(self, token: str) -> dict:
        """Resolve an upload token to a local file path.

        Downloads the uploaded file (if necessary) and returns the local path.

        Args:
            token: The upload token from register_upload.

        Returns:
            Dictionary containing:
                - local_path: Path to the local file
                - filename: The original filename
        """
        ...

    def consume_upload(self, token: str) -> None:
        """Mark an upload token as consumed.

        Cleans up any resources associated with the upload.

        Args:
            token: The upload token to consume.
        """
        ...

    @property
    def is_running(self) -> bool:
        """Whether the server is ready to handle requests."""
        ...

    def ensure_running(self) -> None:
        """Start the server if not already running."""
        ...

    def stop(self) -> None:
        """Stop the server and clean up resources."""
        ...
