"""Tests for GCSFileServer with mocked GCS client."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from jons_mcp_file_server.gcs import GCSFileServer


class TestGCSFileServer:
    """Tests for GCSFileServer class."""

    @pytest.fixture
    def mock_storage(self):
        """Mock google.cloud.storage module."""
        with patch("jons_mcp_file_server.gcs.storage") as mock:
            # Create mock client and bucket
            mock_client = MagicMock()
            mock_bucket = MagicMock()
            mock_client.bucket.return_value = mock_bucket
            mock.Client.return_value = mock_client

            yield {
                "storage": mock,
                "client": mock_client,
                "bucket": mock_bucket,
            }

    @pytest.fixture
    def server(self, mock_storage):
        """Create a GCSFileServer with mocked client."""
        srv = GCSFileServer(
            bucket_name="test-bucket",
            download_ttl=900,
            upload_ttl=300,
        )
        yield srv
        srv.stop()

    @pytest.fixture
    def temp_file(self):
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            f.write("Test content")
            f.flush()
            yield f.name
        if os.path.exists(f.name):
            os.unlink(f.name)

    def test_is_running_always_true(self, server: GCSFileServer) -> None:
        """Test that GCS server is always 'running'."""
        assert server.is_running is True

    def test_ensure_running_initializes_client(
        self, server: GCSFileServer, mock_storage
    ) -> None:
        """Test that ensure_running initializes the GCS client."""
        server.ensure_running()
        mock_storage["storage"].Client.assert_called_once()
        mock_storage["client"].bucket.assert_called_once_with("test-bucket")

    def test_register_download_uploads_file(
        self, server: GCSFileServer, mock_storage, temp_file: str
    ) -> None:
        """Test that register_download uploads file and returns signed URL."""
        mock_blob = MagicMock()
        mock_blob.generate_signed_url.return_value = "https://storage.googleapis.com/signed-url"
        mock_storage["bucket"].blob.return_value = mock_blob

        result = server.register_download(temp_file, "test.txt")

        assert "url" in result
        assert "curl" in result
        assert "token" in result
        assert result["url"] == "https://storage.googleapis.com/signed-url"
        assert "test.txt" in result["curl"]
        mock_blob.upload_from_filename.assert_called_once_with(temp_file)
        mock_blob.generate_signed_url.assert_called_once()

    def test_register_upload_returns_signed_url(
        self, server: GCSFileServer, mock_storage
    ) -> None:
        """Test that register_upload returns signed PUT URL."""
        mock_blob = MagicMock()
        mock_blob.generate_signed_url.return_value = "https://storage.googleapis.com/upload-url"
        mock_storage["bucket"].blob.return_value = mock_blob

        result = server.register_upload(filename="upload.txt", max_bytes=1024)

        assert "uploadUrl" in result
        assert "uploadToken" in result
        assert "method" in result
        assert "expiresIn" in result
        assert "curl" in result
        assert result["uploadUrl"] == "https://storage.googleapis.com/upload-url"
        assert result["method"] == "PUT"
        assert result["expiresIn"] == 300
        mock_blob.generate_signed_url.assert_called_once()

    def test_resolve_upload_downloads_file(
        self, server: GCSFileServer, mock_storage
    ) -> None:
        """Test that resolve_upload downloads from GCS to temp."""
        mock_blob = MagicMock()
        mock_blob.generate_signed_url.return_value = "https://storage.googleapis.com/upload-url"
        mock_storage["bucket"].blob.return_value = mock_blob

        # First register an upload
        result = server.register_upload(filename="uploaded.txt")
        token = result["uploadToken"]

        # Then resolve it
        resolved = server.resolve_upload(token)

        assert "local_path" in resolved
        assert "filename" in resolved
        assert resolved["filename"] == "uploaded.txt"
        mock_blob.download_to_filename.assert_called_once()

    def test_resolve_upload_invalid_token(self, server: GCSFileServer) -> None:
        """Test that resolve_upload raises on invalid token."""
        with pytest.raises(ValueError) as exc_info:
            server.resolve_upload("invalid-token")
        assert "Invalid upload token" in str(exc_info.value)

    def test_consume_upload_removes_token(
        self, server: GCSFileServer, mock_storage
    ) -> None:
        """Test that consume_upload removes the token."""
        mock_blob = MagicMock()
        mock_storage["bucket"].blob.return_value = mock_blob

        result = server.register_upload(filename="to-consume.txt")
        token = result["uploadToken"]

        # Token should be in pending
        assert token in server._pending_uploads

        # Consume it
        server.consume_upload(token)

        # Token should be removed
        assert token not in server._pending_uploads

    def test_stop_cleans_temp_dir(self, server: GCSFileServer) -> None:
        """Test that stop() removes temp directory."""
        temp_dir = server._temp_dir
        assert os.path.exists(temp_dir)

        server.stop()

        assert not os.path.exists(temp_dir)

    def test_credentials_path_used(self, mock_storage) -> None:
        """Test that credentials_path is used for client initialization."""
        server = GCSFileServer(
            bucket_name="test-bucket",
            credentials_path="/path/to/creds.json",
        )

        server.ensure_running()

        mock_storage["storage"].Client.from_service_account_json.assert_called_once_with(
            "/path/to/creds.json"
        )
        server.stop()


class TestGCSDownloadURL:
    """Tests for download URL generation."""

    @pytest.fixture
    def mock_storage(self):
        """Mock google.cloud.storage module."""
        with patch("jons_mcp_file_server.gcs.storage") as mock:
            mock_client = MagicMock()
            mock_bucket = MagicMock()
            mock_client.bucket.return_value = mock_bucket
            mock.Client.return_value = mock_client

            yield {
                "storage": mock,
                "client": mock_client,
                "bucket": mock_bucket,
            }

    @pytest.fixture
    def temp_file(self):
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            f.write("Test content")
            f.flush()
            yield f.name
        if os.path.exists(f.name):
            os.unlink(f.name)

    def test_signed_url_has_correct_method(
        self, mock_storage, temp_file: str
    ) -> None:
        """Test that download URL uses GET method."""
        mock_blob = MagicMock()
        mock_storage["bucket"].blob.return_value = mock_blob

        server = GCSFileServer(bucket_name="test-bucket")
        server.register_download(temp_file, "file.txt")

        # Check that generate_signed_url was called with GET
        call_kwargs = mock_blob.generate_signed_url.call_args.kwargs
        assert call_kwargs["method"] == "GET"
        server.stop()

    def test_upload_url_uses_put_method(self, mock_storage) -> None:
        """Test that upload URL uses PUT method."""
        mock_blob = MagicMock()
        mock_storage["bucket"].blob.return_value = mock_blob

        server = GCSFileServer(bucket_name="test-bucket")
        server.register_upload(filename="file.txt")

        # Check that generate_signed_url was called with PUT
        call_kwargs = mock_blob.generate_signed_url.call_args.kwargs
        assert call_kwargs["method"] == "PUT"
        server.stop()
