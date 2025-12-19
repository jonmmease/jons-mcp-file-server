"""Tests for LocalhostFileServer."""

import json
import os
import tempfile
import time
import urllib.request
import urllib.error

import pytest

from jons_mcp_file_server.localhost import LocalhostFileServer


class TestLocalhostFileServer:
    """Tests for LocalhostFileServer class."""

    @pytest.fixture
    def server(self):
        """Create a test server instance."""
        srv = LocalhostFileServer(download_token_ttl=60, port=0)
        yield srv
        srv.stop()

    @pytest.fixture
    def temp_file(self):
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            f.write("Hello, World!")
            f.flush()
            yield f.name
        if os.path.exists(f.name):
            os.unlink(f.name)

    def test_lazy_initialization(self, server: LocalhostFileServer) -> None:
        """Test that server doesn't start until needed."""
        assert not server.is_running
        assert server.public_base_url is None

    def test_ensure_running_starts_server(self, server: LocalhostFileServer) -> None:
        """Test that ensure_running starts the server."""
        server.ensure_running()
        assert server.is_running
        assert server.public_base_url is not None
        assert server.public_base_url.startswith("http://localhost:")

    def test_ensure_running_is_idempotent(self, server: LocalhostFileServer) -> None:
        """Test that multiple ensure_running calls are safe."""
        server.ensure_running()
        url1 = server.public_base_url
        server.ensure_running()
        url2 = server.public_base_url
        assert url1 == url2

    def test_register_download_returns_url(
        self, server: LocalhostFileServer, temp_file: str
    ) -> None:
        """Test that register_download returns a valid result."""
        result = server.register_download(temp_file, "test.txt")
        assert "token" in result
        assert "url" in result
        assert "curl" in result
        assert result["url"].startswith(server.public_base_url)
        assert "/downloads/" in result["url"]
        assert "test.txt" in result["url"]

    def test_download_file(self, server: LocalhostFileServer, temp_file: str) -> None:
        """Test downloading a registered file."""
        result = server.register_download(temp_file, "test.txt")
        url = result["url"]

        with urllib.request.urlopen(url) as response:
            content = response.read().decode()
            assert content == "Hello, World!"
            assert response.headers.get("Content-Type") == "text/plain"

    def test_invalid_token_returns_404(self, server: LocalhostFileServer) -> None:
        """Test that invalid tokens return 404."""
        server.ensure_running()
        url = f"{server.public_base_url}/downloads/invalid-token/file.txt"

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(url)
        assert exc_info.value.code == 404

    def test_wrong_filename_returns_404(
        self, server: LocalhostFileServer, temp_file: str
    ) -> None:
        """Test that wrong filename returns 404."""
        result = server.register_download(temp_file, "test.txt")
        url = result["url"].replace("test.txt", "wrong.txt")

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(url)
        assert exc_info.value.code == 404

    def test_expired_token_returns_404(self, temp_file: str) -> None:
        """Test that expired tokens return 404."""
        server = LocalhostFileServer(download_token_ttl=1, port=0)
        try:
            result = server.register_download(temp_file, "test.txt")
            url = result["url"]

            time.sleep(1.5)

            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(url)
            assert exc_info.value.code == 404
        finally:
            server.stop()

    def test_stop_cleans_up(self, server: LocalhostFileServer, temp_file: str) -> None:
        """Test that stop() cleans up resources."""
        server.register_download(temp_file, "test.txt")
        assert server.is_running

        server.stop()

        assert not server.is_running
        assert server.public_base_url is None

    def test_cors_headers(self, server: LocalhostFileServer, temp_file: str) -> None:
        """Test that CORS headers are set."""
        result = server.register_download(temp_file, "test.txt")
        url = result["url"]

        with urllib.request.urlopen(url) as response:
            assert response.headers.get("Access-Control-Allow-Origin") == "*"


class TestUploadFunctionality:
    """Tests for upload functionality."""

    @pytest.fixture
    def server(self):
        """Create a test server instance."""
        srv = LocalhostFileServer(upload_token_ttl=60, port=0)
        yield srv
        srv.stop()

    def test_register_upload_returns_info(self, server: LocalhostFileServer) -> None:
        """Test that register_upload returns valid info."""
        result = server.register_upload(filename="test.txt", max_bytes=1024)
        assert "uploadToken" in result
        assert "uploadUrl" in result
        assert "expiresIn" in result
        assert "method" in result
        assert "curl" in result
        assert result["uploadUrl"].startswith(server.public_base_url)
        assert "/uploads" in result["uploadUrl"]
        assert result["expiresIn"] == 60
        assert result["method"] == "POST"

    def test_upload_token_expires(self) -> None:
        """Test that upload tokens expire after TTL."""
        server = LocalhostFileServer(upload_token_ttl=1, port=0)
        try:
            result = server.register_upload()
            token = result["uploadToken"]

            assert token in server._upload_tokens

            time.sleep(1.5)

            server._cleanup_expired_upload_tokens()
            assert token not in server._upload_tokens
        finally:
            server.stop()

    def test_upload_file_success(self, server: LocalhostFileServer) -> None:
        """Test successful file upload via HTTP."""
        result = server.register_upload(filename=None, max_bytes=1024 * 1024)
        upload_url = result["uploadUrl"]
        upload_token = result["uploadToken"]

        boundary = "----TestBoundary12345"
        body = (
            f"------{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="hello.txt"\r\n'
            f"Content-Type: text/plain\r\n"
            f"\r\n"
            f"Hello from test!\r\n"
            f"------{boundary}--\r\n"
        ).encode()

        headers = {
            "Content-Type": f"multipart/form-data; boundary=----{boundary}",
            "X-Upload-Token": upload_token,
        }

        req = urllib.request.Request(upload_url, data=body, headers=headers)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            assert data["success"] is True
            assert data["fileToken"] is not None
            assert data["filename"] == "hello.txt"
            assert data["bytes"] > 0

    def test_upload_missing_token_header(self, server: LocalhostFileServer) -> None:
        """Test that missing X-Upload-Token returns 403."""
        server.ensure_running()

        req = urllib.request.Request(
            f"{server.public_base_url}/uploads",
            data=b"test",
            headers={"Content-Type": "multipart/form-data"},
        )

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 403

    def test_resolve_upload_success(self, server: LocalhostFileServer) -> None:
        """Test resolving a valid upload."""
        result = server.register_upload()
        upload_url = result["uploadUrl"]
        upload_token = result["uploadToken"]

        boundary = "----TestBoundary12345"
        body = (
            f"------{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="resolve-test.txt"\r\n'
            f"Content-Type: text/plain\r\n"
            f"\r\n"
            f"Test content\r\n"
            f"------{boundary}--\r\n"
        ).encode()

        headers = {
            "Content-Type": f"multipart/form-data; boundary=----{boundary}",
            "X-Upload-Token": upload_token,
        }

        req = urllib.request.Request(upload_url, data=body, headers=headers)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            file_token = data["fileToken"]

        resolved = server.resolve_upload(file_token)
        assert "local_path" in resolved
        assert "filename" in resolved
        assert resolved["filename"] == "resolve-test.txt"
        assert os.path.isfile(resolved["local_path"])

    def test_resolve_upload_invalid(self, server: LocalhostFileServer) -> None:
        """Test resolving an invalid token raises error."""
        server.ensure_running()

        with pytest.raises(ValueError) as exc_info:
            server.resolve_upload("invalid-token")
        assert "Invalid upload token" in str(exc_info.value)

    def test_consume_upload(self, server: LocalhostFileServer) -> None:
        """Test that consume_upload removes the token."""
        result = server.register_upload()
        upload_url = result["uploadUrl"]
        upload_token = result["uploadToken"]

        boundary = "----TestBoundary12345"
        body = (
            f"------{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="consume-test.txt"\r\n'
            f"Content-Type: text/plain\r\n"
            f"\r\n"
            f"Test content\r\n"
            f"------{boundary}--\r\n"
        ).encode()

        headers = {
            "Content-Type": f"multipart/form-data; boundary=----{boundary}",
            "X-Upload-Token": upload_token,
        }

        req = urllib.request.Request(upload_url, data=body, headers=headers)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            file_token = data["fileToken"]

        server.resolve_upload(file_token)
        server.consume_upload(file_token)

        with pytest.raises(ValueError):
            server.resolve_upload(file_token)
