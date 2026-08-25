#!/usr/bin/env python3
"""
UiPath Discovery Script - Updated for correct endpoints
"""
import os
import sys
import json
import requests

def get_access_token(client_id, client_secret, tenant_name="DefaultTenant"):
    """Authenticate with UiPath and get access token."""
    print("Authenticating with UiPath...")
    resp = requests.post(
        "https://cloud.uipath.com/identity/connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "OR.AuthAPI"
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
        allow_redirects=False
    )
    if resp.status_code == 200:
        try:
            data = resp.json()
            if "access_token" in data:
                print(f"  ✓ Authenticated. Token: {data['access_token'][:30]}...")
                return data
        except:
            pass
    print(f"  Authentication returned {resp.status_code}")
    if resp.status_code == 302:
        loc = resp.headers.get("Location", "")
        print(f"  Redirected to: {loc}")
    return None

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    client_id = os.getenv("UIPATH_CLIENT_ID", "")
    client_secret = os.getenv("UIPATH_CLIENT_SECRET", "")
    tenant_name = os.getenv("UIPATH_TENANT_NAME", "DefaultTenant")
    org_id = os.getenv("UIPATH_ORG_ID", "")

    if not client_id or not client_secret:
        print("ERROR: UIPATH_CLIENT_ID and UIPATH_CLIENT_SECRET must be set in .env")
        sys.exit(1)

    auth = get_access_token(client_id, client_secret, tenant_name)
    if not auth:
        print("\n=== Cannot discover via API ===")
        print("The UiPath client credentials may not be configured for OAuth.")
        print("You need to register the app in UiPath Cloud:")
        print()
        print("1. Go to https://cloud.uipath.com")
        print("2. Click your profile → Preferences → API Access → Clients")
        print("3. Create a new client with:")
        print("   - Redirect URL: https://cloud.uipath.com")
        print("   - Scopes: OR.AuthAPI, OR.Users.Read, OR.Folders.Read")
        print("   - Grant type: Client Credentials")
        print()
        print("After getting the token, run:")
        print("  https://cloud.uipath.com/identity_api/v1/organizations")
        print("to find your Organization ID.")
        print()
        print("For Environment/Folder ID:")
        print("1. Open Orchestrator in your browser")
        print("2. Look at the URL - the 'fid' parameter is the folder/environment ID")
        print("   Example: https://cloud.uipath.com/{ORG}/{TENANT}/orchestrator_/Default?fid={FOLDER_ID}")
        sys.exit(1)

    token = auth.get("access_token")
    org_id = org_id or input("Enter your Organization ID: ")

    print(f"\nDiscoverting folders in org={org_id}, tenant={tenant_name}...")
    base_url = f"https://cloud.uipath.com/{org_id}/{tenant_name}"
    resp = requests.get(
        f"{base_url}/orchestrator_/odata/Folders?$top=50",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=15
    )
    if resp.status_code == 200:
        folders = resp.json().get("value", [])
        print(f"Found {len(folders)} folders:")
        for f in folders:
            print(f"  • {f.get('DisplayName') or f.get('Name')}: ID={f.get('Id') or f.get('id')}")
        if folders:
            print(f"\nAdd to .env:")
            print(f"  UIPATH_ORG_ID={org_id}")
            print(f"  UIPATH_ENVIRONMENT_ID={folders[0].get('Id') or folders[0].get('id')}")
    else:
        print(f"Folders request failed: {resp.status_code}")
