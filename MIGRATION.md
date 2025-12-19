# Migration Guide: Adopting jons-mcp-file-server

This guide explains how to update MCP servers to use `jons-mcp-file-server` for file transfers, replacing the old ngrok-based localhost server approach.

## Overview

Previously, servers used a local HTTP server with optional ngrok tunneling to expose files to Claude Desktop. The new approach uses:

- **Localhost mode**: Local HTTP server (for local-only access)
- **GCS mode**: Google Cloud Storage signed URLs (for remote access like Claude Desktop)

## Migration Steps

### 1. Add Dependency

Update `pyproject.toml`:

```toml
dependencies = [
    # ... other deps ...
    "jons-mcp-file-server @ git+https://github.com/jonmmease/jons-mcp-file-server.git",
]

# Update Python version requirement
requires-python = ">=3.11"
```

### 2. Add CLI Flag

In your main entry point (e.g., `__main__.py`):

```python
import argparse
import os
import sys

# Global state for GCS mode
gcs_enabled: bool = False

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Your MCP server description",
        prog="your-mcp-server",
    )
    parser.add_argument(
        "--gcs",
        action="store_true",
        help=(
            "Use GCS signed URLs for file transfer (requires GCS_BUCKET env var). "
            "Use this when the MCP client cannot access localhost URLs (e.g., Claude Desktop)."
        ),
    )
    return parser.parse_args()

def main() -> None:
    global gcs_enabled

    args = parse_args()
    gcs_enabled = args.gcs

    # Validate GCS configuration if enabled
    if gcs_enabled:
        if not os.environ.get("GCS_BUCKET"):
            print("Error: --gcs requires GCS_BUCKET environment variable", file=sys.stderr)
            sys.exit(1)

    # ... rest of your server startup ...
```

### 3. Create File Server Helper

Create a helper function to get the configured file server:

```python
def _get_file_server():
    """Get the configured file server instance."""
    from jons_mcp_file_server import get_file_server
    from . import __main__  # or wherever gcs_enabled is defined

    backend = "gcs" if __main__.gcs_enabled else "localhost"
    return get_file_server(backend=backend)
```

### 4. Update File Registration Code

Replace old file serving code with the new API:

#### For Downloads (server → client)

```python
# OLD (ngrok-based)
from .localhost_server import get_localhost_server
server = get_localhost_server()
url = server.register_file(local_path)

# NEW
server = _get_file_server()
registration = server.register_download(local_path, filename)
# registration = {"url": "...", "filename": "...", "curl": "..."}
```

#### For Uploads (client → server)

```python
# OLD (if you had upload support)
# ... various custom implementations ...

# NEW
server = _get_file_server()
registration = server.register_upload(filename="optional.txt", max_bytes=10_000_000)
# registration = {"uploadUrl": "...", "curl": "...", "token": "..."}

# Later, to retrieve the uploaded data:
data = server.consume_upload(token)  # Returns bytes or None
```

### 5. Add Cleanup

In your shutdown/cleanup code:

```python
from jons_mcp_file_server import cleanup_file_server

def shutdown():
    # ... other cleanup ...
    cleanup_file_server()
```

### 6. Remove Old Code

Delete:
- `localhost_server.py` or similar file serving module
- Any ngrok-related code
- `NGROK_AUTHTOKEN` references
- `MCP_FILE_SERVER_PORT` references

## API Quick Reference

### get_file_server()

```python
from jons_mcp_file_server import get_file_server

# Localhost (default)
server = get_file_server()
server = get_file_server(backend="localhost", port=0)

# GCS
server = get_file_server(backend="gcs")  # Uses GCS_BUCKET env var
server = get_file_server(backend="gcs", bucket_name="my-bucket")
```

### register_download()

Register a local file for download by the client:

```python
result = server.register_download("/path/to/file.png", "display_name.png")
# Returns: {"url": "...", "filename": "...", "curl": "curl ..."}
```

### register_upload()

Create an upload endpoint for the client:

```python
result = server.register_upload(filename="data.json", max_bytes=5_000_000)
# Returns: {"uploadUrl": "...", "curl": "curl -X PUT ...", "token": "..."}
```

### consume_upload()

Retrieve uploaded data (one-time use):

```python
data = server.consume_upload(token)  # bytes or None if not found/expired
```

### cleanup_file_server()

Stop the server on shutdown:

```python
from jons_mcp_file_server import cleanup_file_server
cleanup_file_server()
```

## Claude Desktop Configuration

Update your entry in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "your-server": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/repo", "your-server", "--gcs"],
      "env": {
        "GCS_BUCKET": "mcp-file-server-251219",
        "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/mcp-file-server-key.json"
      }
    }
  }
}
```

## Example: Complete Tool Implementation

```python
async def generate_image(prompt: str) -> dict:
    """Generate an image and return a download URL."""
    import tempfile

    # Generate image to temp file
    temp_path = tempfile.mktemp(suffix=".png")
    await some_image_generator(prompt, output_path=temp_path)

    # Register for download
    server = _get_file_server()
    registration = server.register_download(temp_path, "generated_image.png")

    return {
        "url": registration["url"],
        "curl": registration["curl"],
        "prompt": prompt,
    }
```

## Servers to Update

| Server | Repo | Status |
|--------|------|--------|
| jons-mcp-webkit | ~/repos/jons-mcp-webkit | Done |
| jons-mcp-google-workspace | ~/repos/jons-mcp-google-workspace | Pending |
| jons-mcp-nano-banana | ~/repos/jons-mcp-nano-banana | Pending |
