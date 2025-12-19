"""Tests for factory functions."""

import os
from unittest.mock import patch

import pytest

from jons_mcp_file_server import (
    get_file_server,
    cleanup_file_server,
    reset_file_server,
    GCSFileServer,
    LocalhostFileServer,
)


class TestGetFileServer:
    """Tests for get_file_server factory function."""

    def teardown_method(self):
        """Clean up after each test."""
        cleanup_file_server()
        reset_file_server()

    def test_default_selects_localhost(self) -> None:
        """Test that default backend is localhost."""
        server = get_file_server(port=0)
        assert isinstance(server, LocalhostFileServer)
        server.stop()

    def test_explicit_localhost_backend(self) -> None:
        """Test explicit localhost backend selection."""
        server = get_file_server(backend="localhost", port=0)
        assert isinstance(server, LocalhostFileServer)
        server.stop()

    def test_explicit_gcs_backend(self) -> None:
        """Test explicit GCS backend selection."""
        with patch("jons_mcp_file_server.gcs.storage"):
            server = get_file_server(backend="gcs", bucket_name="my-bucket")
            assert isinstance(server, GCSFileServer)

    def test_gcs_without_bucket_raises(self) -> None:
        """Test that GCS backend without bucket raises ValueError."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GCS_BUCKET", None)
            reset_file_server()
            with pytest.raises(ValueError) as exc_info:
                get_file_server(backend="gcs")
            assert "GCS_BUCKET" in str(exc_info.value)

    def test_gcs_uses_env_bucket(self) -> None:
        """Test that GCS backend uses GCS_BUCKET env var."""
        with patch.dict(os.environ, {"GCS_BUCKET": "env-bucket"}):
            with patch("jons_mcp_file_server.gcs.storage"):
                server = get_file_server(backend="gcs")
                assert isinstance(server, GCSFileServer)
                assert server._bucket_name == "env-bucket"

    def test_singleton_returns_same_instance(self) -> None:
        """Test that get_file_server returns the same instance."""
        server1 = get_file_server(backend="localhost", port=0)
        server2 = get_file_server(backend="localhost", port=0)
        assert server1 is server2
        server1.stop()

    def test_localhost_kwargs_passed(self) -> None:
        """Test that localhost kwargs are passed through."""
        server = get_file_server(
            backend="localhost",
            port=0,
            download_token_ttl=1800,
        )
        assert server._download_token_ttl == 1800
        server.stop()

    def test_gcs_kwargs_passed(self) -> None:
        """Test that GCS kwargs are passed through."""
        with patch("jons_mcp_file_server.gcs.storage"):
            server = get_file_server(
                backend="gcs",
                bucket_name="test-bucket",
                download_ttl=600,
                upload_ttl=120,
            )
            assert server._download_ttl == 600
            assert server._upload_ttl == 120


class TestCleanupFileServer:
    """Tests for cleanup_file_server function."""

    def teardown_method(self):
        """Reset after each test."""
        reset_file_server()

    def test_cleanup_stops_server(self) -> None:
        """Test that cleanup stops the server."""
        server = get_file_server(backend="localhost", port=0)
        server.ensure_running()
        assert server.is_running

        cleanup_file_server()

        # Server should be stopped (reference is still valid)
        assert not server.is_running

    def test_cleanup_allows_new_instance(self) -> None:
        """Test that cleanup allows creating a new instance."""
        server1 = get_file_server(backend="localhost", port=0)

        cleanup_file_server()
        reset_file_server()

        server2 = get_file_server(backend="localhost", port=0)
        assert server1 is not server2
        server2.stop()

    def test_cleanup_when_no_server(self) -> None:
        """Test that cleanup is safe when no server exists."""
        reset_file_server()
        cleanup_file_server()  # Should not raise


class TestResetFileServer:
    """Tests for reset_file_server function."""

    def teardown_method(self):
        """Clean up after each test."""
        cleanup_file_server()
        reset_file_server()

    def test_reset_allows_new_config(self) -> None:
        """Test that reset allows creating server with different config."""
        server1 = get_file_server(backend="localhost", port=0, download_token_ttl=100)
        ttl1 = server1._download_token_ttl
        server1.stop()

        reset_file_server()

        server2 = get_file_server(backend="localhost", port=0, download_token_ttl=200)
        ttl2 = server2._download_token_ttl

        assert ttl1 != ttl2
        assert ttl1 == 100
        assert ttl2 == 200
        server2.stop()
