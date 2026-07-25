"""
github_upload.py — Upload QR code images to GitHub repository for persistent storage.

Uses GitHub Content API to commit files to the repository's qrCodes folder.
This ensures QR images survive Render's ephemeral filesystem restarts.
No external dependencies — uses only stdlib urllib + base64.
"""

import os
import base64
import json
import urllib.request
import urllib.error


def _load_github_config():
    """Load GitHub configuration from environment variables."""
    return {
        "token": os.environ.get("GITHUB_TOKEN", "").strip(),
        "owner": os.environ.get("GITHUB_REPO_OWNER", "joyawesome-star").strip(),
        "repo": os.environ.get("GITHUB_REPO_NAME", "kidsheild").strip(),
        "branch": os.environ.get("GITHUB_BRANCH", "main").strip(),
        "path": os.environ.get("QR_CODES_GITHUB_PATH", "qrCodes").strip(),
    }


def _get_raw_url(filename: str) -> str:
    """Build the raw.githubusercontent.com URL for a given filename."""
    cfg = _load_github_config()
    return f"https://raw.githubusercontent.com/{cfg['owner']}/{cfg['repo']}/{cfg['branch']}/{cfg['path']}/{filename}"


def _get_api_url(filename: str) -> str:
    """Build the GitHub API URL for a content file."""
    cfg = _load_github_config()
    return f"https://api.github.com/repos/{cfg['owner']}/{cfg['repo']}/contents/{cfg['path']}/{filename}"


def _get_existing_file_sha(filename: str) -> str | None:
    """
    Check if a file already exists in the repo.
    Returns the SHA if exists, None otherwise.
    """
    cfg = _load_github_config()
    if not cfg["token"]:
        return None

    url = _get_api_url(filename)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {cfg['token']}")
    req.add_header("Accept", "application/vnd.github.v3+json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # File doesn't exist yet
        print(f"[github_upload] Error checking existing file {filename}: {e}")
        return None
    except Exception as e:
        print(f"[github_upload] Error checking existing file {filename}: {e}")
        return None


def upload_qr_image(local_file_path: str) -> str | None:
    """
    Upload a QR image file to GitHub repo's qrCodes folder.

    Args:
        local_file_path: Absolute path to the local QR image file.

    Returns:
        The raw GitHub URL for the uploaded file if successful, None otherwise.
        Example: https://raw.githubusercontent.com/joyawesome-star/kidsheild/main/qrCodes/xxxx.jpg
    """
    cfg = _load_github_config()

    if not cfg["token"]:
        print("[github_upload] GITHUB_TOKEN not set. Skipping GitHub upload.")
        return None

    if not os.path.exists(local_file_path):
        print(f"[github_upload] Local file not found: {local_file_path}")
        return None

    filename = os.path.basename(local_file_path)
    raw_url = _get_raw_url(filename)
    api_url = _get_api_url(filename)

    try:
        # Read the file and base64-encode it
        with open(local_file_path, "rb") as f:
            content_bytes = f.read()
        content_b64 = base64.b64encode(content_bytes).decode("utf-8")

        # Check if file already exists (to get SHA for update)
        existing_sha = _get_existing_file_sha(filename)

        # Build the API request body
        body = {
            "message": f"Upload QR code {filename}",
            "content": content_b64,
            "branch": cfg["branch"],
        }
        if existing_sha:
            body["sha"] = existing_sha

        # Send the PUT request to GitHub API
        data_bytes = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(api_url, data=data_bytes, method="PUT")
        req.add_header("Authorization", f"Bearer {cfg['token']}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=30) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))
            print(f"[github_upload] Successfully uploaded {filename} to GitHub")
            return raw_url

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else "No error body"
        print(f"[github_upload] HTTP Error {e.code} uploading {filename}: {error_body}")
        return None
    except Exception as e:
        print(f"[github_upload] Error uploading {filename}: {e}")
        return None


def upload_qr_images_batch(local_file_paths: list[str]) -> dict[str, str | None]:
    """
    Upload multiple QR images to GitHub.

    Args:
        local_file_paths: List of absolute paths to local QR image files.

    Returns:
        Dictionary mapping local filenames to GitHub raw URLs (or None if failed).
    """
    results = {}
    for path in local_file_paths:
        filename = os.path.basename(path)
        url = upload_qr_image(path)
        results[filename] = url
    return results

