# jons-mcp-file-server

File server abstraction for MCP servers - supports GCS signed URLs and localhost file serving.

## Installation

```bash
pip install jons-mcp-file-server
```

Or with git:
```bash
pip install git+https://github.com/jonmmease/jons-mcp-file-server.git
```

## Usage

```python
from jons_mcp_file_server import get_file_server

# Default: localhost backend
server = get_file_server()

# Explicit GCS backend
server = get_file_server(backend="gcs", bucket_name="my-bucket")

# Register a file for download
result = server.register_download("/path/to/file.png", "screenshot.png")
print(result["url"])  # URL to download the file

# Register an upload endpoint
upload = server.register_upload(filename="upload.txt")
print(upload["uploadUrl"])  # URL to upload to
print(upload["curl"])  # Example curl command
```

## Backends

### Localhost

Runs a local HTTP server for file transfers. URLs are only accessible on the local machine.

```python
server = get_file_server(backend="localhost", port=0)  # 0 = auto-select port
```

### GCS (Google Cloud Storage)

Uses V4 signed URLs for direct upload/download to GCS. This is useful when the MCP client cannot access localhost URLs (e.g., Claude Desktop connecting to a remote server).

```python
server = get_file_server(backend="gcs", bucket_name="my-bucket")
```

Or via environment variable:
```bash
export GCS_BUCKET=my-bucket
```

## GCS Setup Guide

This guide walks you through setting up a GCS bucket for use with jons-mcp-file-server.

### 1. Create a GCS Bucket

```bash
# Create bucket (choose a globally unique name)
gcloud storage buckets create gs://YOUR_BUCKET_NAME \
    --location=us-central1 \
    --uniform-bucket-level-access

# Add lifecycle rule to auto-delete files after 1 day
# (Files are temporary - no need to keep them long term)
cat > /tmp/lifecycle.json << 'EOF'
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {"age": 1}
    }
  ]
}
EOF

gcloud storage buckets update gs://YOUR_BUCKET_NAME \
    --lifecycle-file=/tmp/lifecycle.json
```

### 2. Create a Service Account

Create a service account with minimal permissions (only access to this bucket):

```bash
# Create service account
gcloud iam service-accounts create mcp-file-server \
    --display-name="MCP File Server"

# Get your project ID
PROJECT_ID=$(gcloud config get-value project)

# Grant Storage Object Admin on just this bucket
gcloud storage buckets add-iam-policy-binding gs://YOUR_BUCKET_NAME \
    --member="serviceAccount:mcp-file-server@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/storage.objectAdmin"
```

### 3. Create a Key File

```bash
# Create key file
gcloud iam service-accounts keys create ~/mcp-file-server-key.json \
    --iam-account=mcp-file-server@${PROJECT_ID}.iam.gserviceaccount.com

# Set permissions
chmod 600 ~/mcp-file-server-key.json
```

### 4. Configure Environment

Set the required environment variables:

```bash
export GCS_BUCKET=YOUR_BUCKET_NAME
export GOOGLE_APPLICATION_CREDENTIALS=~/mcp-file-server-key.json
```

### Example: Claude Desktop Configuration

When using with an MCP server in Claude Desktop, add the environment variables to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "my-mcp-server": {
      "command": "uv",
      "args": ["run", "my-mcp-server", "--gcs"],
      "env": {
        "GCS_BUCKET": "YOUR_BUCKET_NAME",
        "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/mcp-file-server-key.json"
      }
    }
  }
}
```

## API Reference

### get_file_server()

Factory function that returns a singleton FileServer instance.

```python
get_file_server(
    backend: Literal["gcs", "localhost"] = "localhost",
    **kwargs
) -> FileServer
```

**Parameters:**
- `backend`: Either `"gcs"` or `"localhost"` (default: `"localhost"`)
- `bucket_name`: GCS bucket name (GCS backend only, or use `GCS_BUCKET` env var)
- `port`: Port for localhost server (default: auto-select)
- `download_token_ttl`: Download URL validity in seconds (localhost: 3600, GCS: 300)
- `upload_token_ttl`: Upload URL validity in seconds (localhost: 300, GCS: 60)

### FileServer.register_download()

Register a local file for download.

```python
register_download(file_path: str, filename: str) -> dict
```

Returns `{"url": "...", "filename": "..."}`.

### FileServer.register_upload()

Register an upload endpoint.

```python
register_upload(filename: str | None = None) -> dict
```

Returns `{"uploadUrl": "...", "curl": "...", "token": "..."}`.

### FileServer.resolve_upload()

Get the upload URL for a token.

```python
resolve_upload(token: str) -> str | None
```

### FileServer.consume_upload()

Get and remove uploaded file data.

```python
consume_upload(token: str) -> bytes | None
```

### cleanup_file_server()

Stop the singleton file server if running.

### reset_file_server()

Reset the singleton to allow creating a new instance with different configuration.

## License

MIT
