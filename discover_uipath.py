#!/usr/bin/env python3
"""
UiPath Discovery Script — Modern Automation Cloud folder model

Usage:
    python3 discover_uipath.py

Discovers:
  - Organizations accessible to the configured credentials
  - Folders (modern model) available in the organization
  - Processes/Releases within a folder

Authentication:
  1. PAT (Personal Access Token) — used directly as Bearer
  2. Client Credentials — OAuth2 client_credentials flow

Configuration priority: client credentials > PAT
"""
import os
import sys
import json
import time
import requests
from typing import Optional

UIPATH_IDENTITY_TOKEN = "https://cloud.uipath.com/identity_/connect/token"
UIPATH_BASE = "https://cloud.uipath.com"


def authenticate(client_id: str, client_secret: str, tenant: str, pat: Optional[str]) -> str:
    """Authenticate with UiPath and return an access token."""
    if pat:
        print("Authenticating with UiPath via PAT...")
        return pat

    if not client_id or not client_secret:
        print("ERROR: Either UIPATH_PAT or UIPATH_CLIENT_ID + UIPATH_CLIENT_SECRET must be set.")
        sys.exit(1)

    print("Authenticating with UiPath via client credentials...")
    resp = requests.post(
        UIPATH_IDENTITY_TOKEN,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "OR.Default",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    if resp.status_code == 200:
        data = resp.json()
        token = data.get("access_token")
        if token:
            print("  Authenticated successfully.")
            return token
    print(f"  Authentication failed: {resp.status_code} - {resp.text[:200]}")
    sys.exit(1)


def get_folders(token: str, org_id: str, tenant: str) -> list:
    """List all folders in the organization using the modern folder model."""
    url = f"{UIPATH_BASE}/{org_id}/{tenant}/orchestrator_/odata/Folders?$top=100"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=15)
    if resp.status_code == 200:
        return resp.json().get("value", [])
    print(f"  Failed to list folders: {resp.status_code} - {resp.text[:200]}")
    return []


def get_releases(token: str, org_id: str, tenant: str, folder_path: str) -> list:
    """List available releases/processes in a specific folder."""
    url = f"{UIPATH_BASE}/{org_id}/{tenant}/orchestrator_/odata/Releases?$top=100"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "X-UIPATH-FolderPath": folder_path,
    }
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code == 200:
        return resp.json().get("value", [])
    print(f"  Failed to list releases: {resp.status_code} - {resp.text[:200]}")
    return []


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    client_id = os.getenv("UIPATH_CLIENT_ID", "")
    client_secret = os.getenv("UIPATH_CLIENT_SECRET", "")
    tenant = os.getenv("UIPATH_TENANT_NAME", "DefaultTenant")
    org_id = os.getenv("UIPATH_ORG_ID", "")
    pat = os.getenv("UIPATH_PAT", "")
    folder_path = os.getenv("UIPATH_TEST_FOLDER", "Shared/Ghost-QA")

    if not org_id:
        print("ERROR: UIPATH_ORG_ID is required in .env")
        print("  Log in to https://cloud.uipath.com and copy the org identifier from the URL.")
        sys.exit(1)

    token = authenticate(client_id, client_secret, tenant, pat)

    print(f"\nDiscovering folders in org='{org_id}', tenant='{tenant}'...")
    folders = get_folders(token, org_id, tenant)

    if not folders:
        print("  No folders found. Check that your credentials have folder access.")
        print("\n  To create a folder:")
        print("    1. Go to https://cloud.uipath.com/{org_id}/{tenant_name}")
        print("    2. Navigate to Orchestrator → Folders")
        print("    3. Create a new folder with path: 'Shared/Ghost-QA'")
        sys.exit(1)

    print(f"\n  Found {len(folders)} folder(s):")
    for f in folders:
        name = f.get("DisplayName") or f.get("Name", "N/A")
        fid = f.get("Id") or f.get("id")
        fkey = f.get("Key") or f.get("key")
        print(f"    - {name} (Id={fid}, Key={fkey})")

    # Resolve configured folder
    matching = [f for f in folders if (f.get("DisplayName") or f.get("Name")) == folder_path]
    if matching:
        f = matching[0]
        print(f"\n  Configured folder '{folder_path}' resolved:")
        print(f"    Id: {f.get('Id') or f.get('id')}")
        print(f"    Key: {f.get('Key') or f.get('key')}")

        # List processes
        releases = get_releases(token, org_id, tenant, folder_path)
        if releases:
            print(f"\n  Processes in folder '{folder_path}':")
            for r in releases[:20]:
                print(f"    - {r.get('Name', 'N/A')} (Key={r.get('Key', '')[:12]}...)")
                print(f"    Version: {r.get('Version', 'N/A')}")
        else:
            print(f"\n  No processes found in folder '{folder_path}'.")
            print(f"  Deploy a UiPath project to this folder in Orchestrator.")
    else:
        print(f"\n  WARNING: Folder '{folder_path}' not found among available folders.")
        print(f"  Available: {[f.get('DisplayName') or f.get('Name') for f in folders]}")

    print(f"\n  Add to .env:")
    print(f"    UIPATH_ORG_ID={org_id}")
    if matching:
        print(f"    UIPATH_TEST_FOLDER={folder_path}")


if __name__ == "__main__":
    main()
