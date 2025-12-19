"""LocalhostFileServer - HTTP server for file uploads and downloads.

Provides:
- HTTP server to serve files (e.g., screenshots, browser downloads)
- HTTP endpoint for receiving file uploads
- Optional ngrok tunnel for public URL access
- Security via session-scoped token whitelist with TTL
- Lazy initialization (only starts on first use)
- Automatic cleanup of expired tokens and files
"""

import http.server
import json
import logging
import mimetypes
import os
import re
import shutil
import socketserver
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, unquote
from uuid import uuid4

logger = logging.getLogger(__name__)

# Default port for the file server (fixed for reliable ngrok tunnel management)
# Override with MCP_FILE_SERVER_PORT environment variable if needed
DEFAULT_FILE_SERVER_PORT = 9171

# Default TTL for download tokens (1 hour)
DEFAULT_DOWNLOAD_TOKEN_TTL = 60 * 60

# Default TTL for upload tokens (5 minutes)
DEFAULT_UPLOAD_TOKEN_TTL = 5 * 60

# Default max upload size (50MB)
DEFAULT_MAX_UPLOAD_SIZE = 50 * 1024 * 1024

# Cleanup interval for expired tokens (10 minutes)
CLEANUP_INTERVAL = 10 * 60


@dataclass
class Download:
    """Registered download entry."""

    local_path: str
    filename: str
    registered_at: float


@dataclass
class UploadToken:
    """Pending upload token entry."""

    filename: str | None
    max_bytes: int
    created_at: float


@dataclass
class UploadedFile:
    """Uploaded file entry."""

    local_path: str
    filename: str
    uploaded_at: float


class LocalhostFileServer:
    """HTTP server for file uploads and downloads with token-based access control.

    Args:
        ngrok: If True, create ngrok tunnel for public URLs (requires NGROK_AUTHTOKEN)
        download_token_ttl: TTL for download tokens in seconds (default 1 hour)
        upload_token_ttl: TTL for upload tokens in seconds (default 5 minutes)
        port: Port to listen on. None uses DEFAULT_FILE_SERVER_PORT or MCP_FILE_SERVER_PORT
              env var. Pass 0 to let OS assign a random port (useful for tests).
    """

    def __init__(
        self,
        ngrok: bool = False,
        download_token_ttl: int = DEFAULT_DOWNLOAD_TOKEN_TTL,
        upload_token_ttl: int = DEFAULT_UPLOAD_TOKEN_TTL,
        port: int | None = None,
    ):
        self._ngrok_enabled = ngrok
        self._download_token_ttl = download_token_ttl
        self._upload_token_ttl = upload_token_ttl
        self._requested_port = port  # None = use default, 0 = random
        self._downloads: dict[str, Download] = {}
        self._upload_tokens: dict[str, UploadToken] = {}
        self._uploaded_files: dict[str, UploadedFile] = {}
        self._server: Optional[socketserver.TCPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._cleanup_timer: Optional[threading.Timer] = None
        self._port: Optional[int] = None
        self._public_base_url: Optional[str] = None
        self._ngrok_tunnel = None
        self._lock = threading.Lock()
        self._is_running = False
        self._upload_dir: Optional[str] = None

    @property
    def is_running(self) -> bool:
        """Check if the server is running."""
        return self._is_running

    @property
    def public_base_url(self) -> Optional[str]:
        """Get the public base URL (localhost or ngrok)."""
        return self._public_base_url

    def ensure_running(self) -> None:
        """Ensure the server is running (lazy initialization).

        Creates HTTP server on first call. ngrok tunnel is started if configured.
        """
        with self._lock:
            if self._is_running:
                return

            # Create handler class with reference to this server
            server_ref = self

            class RequestHandler(http.server.BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: object) -> None:
                    # Suppress logging
                    pass

                def do_OPTIONS(self) -> None:
                    """Handle CORS preflight."""
                    self.send_response(204)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    self.send_header(
                        "Access-Control-Allow-Headers",
                        "Content-Type, X-Upload-Token",
                    )
                    self.end_headers()

                def do_GET(self) -> None:
                    """Handle GET requests."""
                    # Parse URL: /downloads/{token}/{filename}
                    path_parts = self.path.split("/")
                    if len(path_parts) != 4 or path_parts[1] != "downloads":
                        self.send_response(404)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(b"Not Found")
                        return

                    token = path_parts[2]
                    requested_filename = unquote(path_parts[3])

                    # Look up download
                    download = server_ref._downloads.get(token)
                    if not download:
                        self.send_response(404)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(b"Not Found")
                        return

                    # Check expiration
                    if time.time() - download.registered_at > server_ref._download_token_ttl:
                        del server_ref._downloads[token]
                        self.send_response(404)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(b"Download link expired")
                        return

                    # Verify filename matches
                    if requested_filename != download.filename:
                        self.send_response(404)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(b"Not Found")
                        return

                    # Verify file exists
                    if not os.path.isfile(download.local_path):
                        self.send_response(404)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(b"File not found")
                        return

                    # Serve the file
                    try:
                        file_size = os.path.getsize(download.local_path)
                        mime_type, _ = mimetypes.guess_type(download.local_path)
                        if not mime_type:
                            mime_type = "application/octet-stream"

                        self.send_response(200)
                        self.send_header("Content-Type", mime_type)
                        self.send_header("Content-Length", str(file_size))
                        self.send_header(
                            "Content-Disposition",
                            f'attachment; filename="{quote(download.filename)}"',
                        )
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()

                        with open(download.local_path, "rb") as f:
                            self.wfile.write(f.read())
                    except Exception:
                        self.send_response(500)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(b"Internal Server Error")

                def do_POST(self) -> None:
                    """Handle POST requests for file uploads."""
                    # Only /uploads endpoint
                    if self.path != "/uploads":
                        self.send_response(404)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(b"Not Found")
                        return

                    # Validate upload token header
                    upload_token = self.headers.get("X-Upload-Token")
                    if not upload_token:
                        self.send_response(403)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            json.dumps({"error": "Missing X-Upload-Token header"}).encode()
                        )
                        return

                    # Look up and validate token
                    token_entry = server_ref._upload_tokens.get(upload_token)
                    if not token_entry:
                        self.send_response(403)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            json.dumps({"error": "Invalid upload token"}).encode()
                        )
                        return

                    # Check token expiration
                    if time.time() - token_entry.created_at > server_ref._upload_token_ttl:
                        del server_ref._upload_tokens[upload_token]
                        self.send_response(403)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            json.dumps({"error": "Upload token expired"}).encode()
                        )
                        return

                    # Check content length
                    content_length = int(self.headers.get("Content-Length", 0))
                    if content_length > token_entry.max_bytes:
                        self.send_response(413)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            json.dumps({
                                "error": f"File too large. Max size: {token_entry.max_bytes} bytes"
                            }).encode()
                        )
                        return

                    # Parse multipart/form-data
                    content_type = self.headers.get("Content-Type", "")
                    if not content_type.startswith("multipart/form-data"):
                        self.send_response(400)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            json.dumps({"error": "Content-Type must be multipart/form-data"}).encode()
                        )
                        return

                    try:
                        # Extract boundary from content type
                        boundary_match = re.search(r'boundary=(.+)', content_type)
                        if not boundary_match:
                            raise ValueError("No boundary in Content-Type")
                        boundary = boundary_match.group(1).strip('"')

                        # Read and parse body
                        body = self.rfile.read(content_length)
                        file_data, filename = server_ref._parse_multipart(body, boundary)

                        if file_data is None:
                            self.send_response(400)
                            self.send_header("Access-Control-Allow-Origin", "*")
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            self.wfile.write(
                                json.dumps({"error": "No file found in request"}).encode()
                            )
                            return

                        # Use token's expected filename if provided
                        if token_entry.filename:
                            filename = token_entry.filename

                        # Generate file token and save file
                        file_token = str(uuid4())
                        safe_filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
                        upload_dir = server_ref._upload_dir
                        assert upload_dir is not None  # Set during ensure_running()
                        save_path = os.path.join(
                            upload_dir,
                            f"{file_token}-{safe_filename}",
                        )

                        with open(save_path, "wb") as f:
                            f.write(file_data)

                        # Register uploaded file
                        server_ref._uploaded_files[file_token] = UploadedFile(
                            local_path=save_path,
                            filename=filename,
                            uploaded_at=time.time(),
                        )

                        # Consume upload token (single use)
                        del server_ref._upload_tokens[upload_token]

                        # Return success
                        self.send_response(200)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            json.dumps({
                                "success": True,
                                "fileToken": file_token,
                                "filename": filename,
                                "bytes": len(file_data),
                            }).encode()
                        )

                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            json.dumps({"error": f"Upload failed: {str(e)}"}).encode()
                        )

            # Determine port (fixed port enables reliable ngrok tunnel cleanup)
            if self._requested_port is not None:
                port = self._requested_port
            else:
                env_port = os.environ.get("MCP_FILE_SERVER_PORT")
                port = int(env_port) if env_port else DEFAULT_FILE_SERVER_PORT

            # Create upload directory
            self._upload_dir = tempfile.mkdtemp(prefix="mcp-uploads-")

            # Create server
            self._server = socketserver.TCPServer(("127.0.0.1", port), RequestHandler)
            self._port = self._server.server_address[1]
            self._public_base_url = f"http://localhost:{self._port}"

            # Start server thread
            self._server_thread = threading.Thread(
                target=self._server.serve_forever, daemon=True
            )
            self._server_thread.start()

            # Start ngrok if configured
            if self._ngrok_enabled:
                self._start_ngrok()

            # Start cleanup timer
            self._schedule_cleanup()

            self._is_running = True

    def _parse_multipart(
        self, body: bytes, boundary: str
    ) -> tuple[bytes | None, str]:
        """Parse multipart/form-data body to extract file content.

        Args:
            body: Raw request body bytes
            boundary: Multipart boundary string

        Returns:
            Tuple of (file_data, filename) or (None, "") if no file found
        """
        boundary_bytes = f"--{boundary}".encode()
        parts = body.split(boundary_bytes)

        for part in parts:
            if not part or part == b"--\r\n" or part == b"--":
                continue

            # Split headers from content
            if b"\r\n\r\n" not in part:
                continue

            header_section, content = part.split(b"\r\n\r\n", 1)
            headers = header_section.decode("utf-8", errors="replace")

            # Look for Content-Disposition with filename
            if 'Content-Disposition' in headers and 'filename=' in headers:
                # Extract filename
                filename_match = re.search(r'filename="([^"]+)"', headers)
                if filename_match:
                    filename = filename_match.group(1)
                    # Remove trailing boundary markers
                    if content.endswith(b"\r\n"):
                        content = content[:-2]
                    return content, filename

        return None, ""

    def _start_ngrok(self) -> None:
        """Start ngrok tunnel."""
        auth_token = os.environ.get("NGROK_AUTHTOKEN")
        if not auth_token:
            raise ValueError(
                "NGROK_AUTHTOKEN environment variable is required when ngrok is enabled. "
                "Get your auth token from https://dashboard.ngrok.com/get-started/your-authtoken"
            )

        from pyngrok import conf, ngrok
        from pyngrok.exception import PyngrokNgrokError

        conf.get_default().auth_token = auth_token

        # Disconnect any existing tunnel to our port (from crashed sessions)
        # This is more surgical than ngrok.kill() which would affect other servers
        try:
            for tunnel in ngrok.get_tunnels():
                # Check if this tunnel forwards to our port
                tunnel_addr = tunnel.config.get("addr", "")
                if str(self._port) in str(tunnel_addr):
                    logger.info(f"Disconnecting stale tunnel to port {self._port}")
                    ngrok.disconnect(tunnel.public_url)
        except Exception:
            pass  # Ignore errors if ngrok isn't running

        try:
            self._ngrok_tunnel = ngrok.connect(self._port, "http")
        except PyngrokNgrokError as e:
            # ERR_NGROK_334: endpoint already online (stale tunnel from crash)
            # Kill the local ngrok process and retry - this clears stale tunnels
            if "ERR_NGROK_334" in str(e):
                logger.warning("Stale ngrok tunnel detected, killing ngrok and retrying")
                try:
                    ngrok.kill()
                except Exception:
                    pass
                # Retry connection
                self._ngrok_tunnel = ngrok.connect(self._port, "http")
            else:
                raise

        self._public_base_url = self._ngrok_tunnel.public_url

    def _schedule_cleanup(self) -> None:
        """Schedule periodic cleanup of expired tokens."""

        def cleanup() -> None:
            self._cleanup_expired_downloads()
            self._cleanup_expired_upload_tokens()
            if self._is_running:
                self._cleanup_timer = threading.Timer(CLEANUP_INTERVAL, cleanup)
                self._cleanup_timer.daemon = True
                self._cleanup_timer.start()

        self._cleanup_timer = threading.Timer(CLEANUP_INTERVAL, cleanup)
        self._cleanup_timer.daemon = True
        self._cleanup_timer.start()

    def _cleanup_expired_downloads(self) -> None:
        """Remove expired download registrations."""
        now = time.time()
        expired = [
            token
            for token, download in self._downloads.items()
            if now - download.registered_at > self._download_token_ttl
        ]
        for token in expired:
            del self._downloads[token]

    def _cleanup_expired_upload_tokens(self) -> None:
        """Remove expired upload tokens."""
        now = time.time()
        expired = [
            token
            for token, entry in self._upload_tokens.items()
            if now - entry.created_at > self._upload_token_ttl
        ]
        for token in expired:
            del self._upload_tokens[token]

    def register_download(self, local_path: str, filename: str) -> dict:
        """Register a file for serving.

        Args:
            local_path: Absolute path to the file
            filename: Filename to use in the download URL

        Returns:
            Dict with 'url', 'curl', and 'token' keys
        """
        self.ensure_running()

        # Clean up expired before registering new
        self._cleanup_expired_downloads()

        token = str(uuid4())
        self._downloads[token] = Download(
            local_path=local_path,
            filename=filename,
            registered_at=time.time(),
        )

        url = f"{self._public_base_url}/downloads/{token}/{quote(filename)}"
        return {
            "url": url,
            "curl": f"curl -o '{filename}' '{url}'",
            "token": token,
        }

    def register_upload(
        self,
        filename: str | None = None,
        max_bytes: int = DEFAULT_MAX_UPLOAD_SIZE,
    ) -> dict:
        """Register an upload token for receiving a file upload.

        Args:
            filename: Optional expected filename (overrides uploaded filename)
            max_bytes: Maximum allowed file size in bytes

        Returns:
            Dict with uploadUrl, uploadToken, method, expiresIn, and curl
        """
        self.ensure_running()

        # Clean up expired tokens first
        self._cleanup_expired_upload_tokens()

        token = str(uuid4())
        self._upload_tokens[token] = UploadToken(
            filename=filename,
            max_bytes=max_bytes,
            created_at=time.time(),
        )

        upload_url = f"{self._public_base_url}/uploads"
        safe_filename = filename or "file"
        return {
            "uploadUrl": upload_url,
            "uploadToken": token,
            "method": "POST",  # Localhost uses multipart/form-data POST
            "expiresIn": self._upload_token_ttl,
            "curl": f"curl -X POST -H 'X-Upload-Token: {token}' -F 'file=@{safe_filename}' '{upload_url}'",
        }

    def resolve_upload(self, token: str) -> dict:
        """Resolve an upload token to the local file path.

        Args:
            token: Token returned from successful upload (fileToken from response)

        Returns:
            Dict with 'local_path' and 'filename' keys

        Raises:
            ValueError: If token is invalid or file not found
        """
        uploaded = self._uploaded_files.get(token)
        if not uploaded:
            raise ValueError(f"Invalid upload token: {token}")

        if not os.path.isfile(uploaded.local_path):
            del self._uploaded_files[token]
            raise ValueError(f"Uploaded file no longer exists: {token}")

        return {
            "local_path": uploaded.local_path,
            "filename": uploaded.filename,
        }

    def consume_upload(self, token: str) -> None:
        """Mark an upload token as consumed (removes from tracking).

        Args:
            token: Token to consume
        """
        if token in self._uploaded_files:
            del self._uploaded_files[token]

    def stop(self) -> None:
        """Stop the server and clean up resources."""
        with self._lock:
            if not self._is_running:
                return

            # Stop cleanup timer
            if self._cleanup_timer:
                self._cleanup_timer.cancel()
                self._cleanup_timer = None

            # Close ngrok tunnel
            if self._ngrok_tunnel:
                try:
                    from pyngrok import ngrok

                    ngrok.disconnect(self._ngrok_tunnel.public_url)
                    ngrok.kill()
                except Exception:
                    pass
                self._ngrok_tunnel = None

            # Shutdown HTTP server
            if self._server:
                self._server.shutdown()
                self._server = None

            # Clean up uploaded files
            for uploaded in self._uploaded_files.values():
                try:
                    if os.path.isfile(uploaded.local_path):
                        os.remove(uploaded.local_path)
                except Exception:
                    pass

            # Clean up upload directory
            if self._upload_dir and os.path.isdir(self._upload_dir):
                try:
                    shutil.rmtree(self._upload_dir)
                except Exception:
                    pass
                self._upload_dir = None

            # Clear state
            self._downloads.clear()
            self._upload_tokens.clear()
            self._uploaded_files.clear()
            self._public_base_url = None
            self._port = None
            self._is_running = False
