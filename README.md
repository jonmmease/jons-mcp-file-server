# jons-mcp-file-server

File server abstraction for MCP servers - supports GCS signed URLs and localhost file serving.

## Installation

```bash
pip install jons-mcp-file-server
```

Or with git:
```bash
pip install git+https://github.com/jmease/jons-mcp-file-server.git
```

## Usage

```python
from jons_mcp_file_server import get_file_server

# Auto-detect backend (GCS if GCS_BUCKET set, else localhost)
server = get_file_server()

# Register a file for download
result = server.register_download("/path/to/file.png", "screenshot.png")
print(result["url"])  # URL to download the file

# Register an upload endpoint
upload = server.register_upload(filename="upload.txt")
print(upload["uploadUrl"])  # URL to upload to
print(upload["curl"])  # Example curl command
```

## Backends

### GCS (Google Cloud Storage)

Uses signed URLs for direct upload/download to GCS.

```bash
export GCS_BUCKET=my-bucket
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

### Localhost

Runs a local HTTP server. Optionally uses ngrok for public URLs.

```bash
export NGROK_AUTHTOKEN=your-token  # Only if using ngrok
```
