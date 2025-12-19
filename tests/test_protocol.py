"""Tests for FileServer protocol."""

from jons_mcp_file_server.protocol import FileServer
from jons_mcp_file_server.gcs import GCSFileServer
from jons_mcp_file_server.localhost import LocalhostFileServer


def test_gcs_file_server_implements_protocol():
    """Test that GCSFileServer implements the FileServer protocol."""
    assert isinstance(GCSFileServer("test-bucket"), FileServer)


def test_localhost_file_server_implements_protocol():
    """Test that LocalhostFileServer implements the FileServer protocol."""
    server = LocalhostFileServer(ngrok=False, port=0)
    try:
        assert isinstance(server, FileServer)
    finally:
        server.stop()


def test_protocol_has_required_methods():
    """Test that FileServer protocol defines required methods."""
    required_methods = [
        "register_download",
        "register_upload",
        "resolve_upload",
        "consume_upload",
        "is_running",
        "ensure_running",
        "stop",
    ]
    for method in required_methods:
        assert hasattr(FileServer, method), f"FileServer missing method: {method}"
