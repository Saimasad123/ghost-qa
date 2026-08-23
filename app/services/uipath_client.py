import json
import logging
import time
import requests
from typing import Dict, List, Any, Optional, Tuple
from app.config import settings

logger = logging.getLogger(__name__)

UIPATH_BASE_URL = "https://cloud.uipath.com"
UIPATH_TOKEN_ENDPOINT = "https://cloud.uipath.com/identity_/connect/token"
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_POLL_INTERVAL = 5
DEFAULT_POLL_TIMEOUT = 300


class UiPathAuthError(Exception):
    pass


class UiPathAPIError(Exception):
    pass


class UiPathNotFoundError(Exception):
    pass


class UiPathClient:
    """
    Modern UiPath Automation Cloud API client.

    Authentication:
        1. PAT (Personal Access Token) — used directly as a Bearer token.
        2. Client Credentials — OAuth2 client_credentials grant against Identity Server.

        If both are configured, client credentials take priority for
        unattended production/server operation. PAT is intended for
        development/testing.
    """

    def __init__(self):
        self.client_id = settings.UIPATH_CLIENT_ID
        self.client_secret = settings.UIPATH_CLIENT_SECRET
        self.tenant_name = settings.UIPATH_TENANT_NAME or "DefaultTenant"
        self.org_id = settings.UIPATH_ORG_ID
        self.folder_path = settings.UIPATH_TEST_FOLDER
        self.test_process = settings.UIPATH_TEST_PROCESS
        self.pat = settings.UIPATH_PAT

        self.access_token: Optional[str] = None
        self.token_expires_at: float = 0
        self.folder_id: Optional[int] = None
        self.folder_key: Optional[str] = None
        self.release_key: Optional[str] = None
        self._folder_resolved = False
        self._release_resolved = False

    @property
    def base_url(self) -> str:
        if not self.org_id:
            raise UiPathAuthError("UIPATH_ORG_ID is required for UiPath API calls")
        return f"{UIPATH_BASE_URL}/{self.org_id}/{self.tenant_name}"

    def has_confidential_app(self) -> bool:
        return bool(self.client_id and self.client_secret and self.tenant_name)

    def has_pat(self) -> bool:
        return bool(self.pat)

    def is_configured(self) -> bool:
        return self.has_confidential_app() or self.has_pat()

    def _authenticate_client_credentials(self) -> str:
        """Authenticate using OAuth2 client_credentials grant."""
        scope = "OR.Default"
        resp = requests.post(
            UIPATH_TOKEN_ENDPOINT,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": scope,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            raise UiPathAuthError(
                f"Client credentials auth failed: HTTP {resp.status_code} - "
                f"{resp.text[:200]}"
            )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise UiPathAuthError("Token response missing access_token")
        return token

    def _authenticate_pat(self) -> str:
        """Authenticate using a Personal Access Token (used directly as Bearer)."""
        if not self.pat:
            raise UiPathAuthError("UIPATH_PAT is configured but empty")
        return self.pat

    def get_access_token(self) -> str:
        """
        Obtain an access token.

        Priority:
        1. Client credentials (if client_id + client_secret + tenant are set)
        2. PAT (if set)
        """
        if self.access_token and time.time() < self.token_expires_at:
            return self.access_token

        if self.has_confidential_app():
            logger.info("Authenticating with UiPath via client credentials")
            token = self._authenticate_client_credentials()
            self.access_token = token
            self.token_expires_at = time.time() + 3500
            return token

        if self.has_pat():
            logger.info("Authenticating with UiPath via PAT")
            token = self._authenticate_pat()
            self.access_token = token
            self.token_expires_at = time.time() + 3500
            return token

        raise UiPathAuthError(
            "No UiPath credentials configured. "
            "Set UIPATH_PAT or UIPATH_CLIENT_ID + UIPATH_CLIENT_SECRET + UIPATH_TENANT_NAME."
        )

    def _headers(self, include_folder: bool = True) -> Dict[str, str]:
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if include_folder and self.folder_id:
            headers["X-UIPATH-OrganizationUnitId"] = str(self.folder_id)
        return headers

    def _request(
        self, method: str, path: str, include_folder: bool = True, **kwargs
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("headers", self._headers(include_folder))
        kwargs.setdefault("timeout", DEFAULT_REQUEST_TIMEOUT)
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.exceptions.Timeout:
            raise UiPathAPIError(f"UiPath API request timed out: {method} {path}")
        except requests.exceptions.ConnectionError as e:
            raise UiPathAPIError(f"UiPath API connection error: {e}")
        if resp.status_code == 401:
            raise UiPathAuthError(f"Authentication failed: HTTP 401")
        if resp.status_code == 404:
            raise UiPathNotFoundError(f"Resource not found: {path}")
        if resp.status_code >= 400:
            raise UiPathAPIError(
                f"UiPath API error: HTTP {resp.status_code} - {resp.text[:300]}"
            )
        if resp.status_code == 204:
            return {}
        return resp.json()

    def resolve_folder(self) -> Tuple[int, str]:
        """
        Resolve the configured folder path (e.g. 'Shared/Ghost-QA') to its
        numeric Id and stable Key via the Orchestrator OData Folders endpoint.

        Returns:
            (folder_id, folder_key)
        """
        if self._folder_resolved and self.folder_id and self.folder_key:
            return self.folder_id, self.folder_key

        if not self.folder_path:
            raise UiPathAPIError("UIPATH_TEST_FOLDER is not configured")

        try:
            data = self._request(
                "GET",
                "/orchestrator_/odata/Folders",
                include_folder=False,
            )
        except UiPathAPIError:
            data = self._request(
                "GET",
                "/orchestrator_/odata/folders",
                include_folder=False,
            )

        folders = data.get("value", [])
        for f in folders:
            display = f.get("DisplayName") or f.get("Name", "")
            if display == self.folder_path or display == self.folder_path.replace("/", "\\"):
                self.folder_id = f.get("Id") or f.get("id")
                self.folder_key = f.get("Key") or f.get("key")
                self._folder_resolved = True
                logger.info(f"Resolved folder '{self.folder_path}' -> id={self.folder_id}")
                return self.folder_id, self.folder_key

        raise UiPathNotFoundError(
            f"Folder '{self.folder_path}' not found in organization "
            f"'{self.org_id}'. "
            f"Available folders: {[f.get('DisplayName') or f.get('Name') for f in folders]}"
        )

    def resolve_release(self) -> str:
        """
        Resolve the configured UiPath process/package name to its Release Key
        within the resolved folder.

        Returns:
            release_key string
        """
        if self._release_resolved and self.release_key:
            return self.release_key

        if not self.test_process:
            raise UiPathAPIError(
                "UIPATH_TEST_PROCESS is not configured. "
                "Set it to the name of your UiPath process/package."
            )

        self.resolve_folder()

        try:
            data = self._request(
                "GET",
                "/orchestrator_/odata/Releases",
            )
        except UiPathAPIError:
            data = self._request(
                "GET",
                "/orchestrator_/odata/releases",
            )

        releases = data.get("value", [])
        for r in releases:
            if r.get("Name") == self.test_process:
                self.release_key = r.get("Key") or r.get("key")
                self._release_resolved = True
                logger.info(f"Resolved process '{self.test_process}' -> release_key={self.release_key[:12]}...")
                return self.release_key

        raise UiPathNotFoundError(
            f"Process '{self.test_process}' not found in folder "
            f"'{self.folder_path}'. "
            f"Available processes: {[r.get('Name') for r in releases]}"
        )

    def start_job(self, input_arguments: Optional[Dict[str, Any]] = None) -> str:
        """
        Start a UiPath job for the configured process in the resolved folder.

        Returns:
            job_id (string)
        """
        release_key = self.resolve_release()

        body = {
            "startImmediately": True,
            "inputArguments": json.dumps(input_arguments or {}),
        }

        resp = self._request(
            "POST",
            "/orchestrator_/odata/Jobs/UiPath.Server.Configuration.OData.StartJob",
            json=body,
        )

        job_id = resp.get("Id") or resp.get("id") or resp.get("JobId")
        if not job_id:
            job_id = resp.get("value", [{}])[0].get("Id") if resp.get("value") else None
        if not job_id:
            raise UiPathAPIError("StartJob response did not contain a job Id")

        logger.info(f"Started UiPath job {job_id} (release_key: {release_key[:8]}...)")
        return str(job_id)

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Retrieve the status of a specific job."""
        data = self._request("GET", f"/orchestrator_/odata/Jobs({job_id})")
        return data

    def poll_job(
        self,
        job_id: str,
        timeout: int = DEFAULT_POLL_TIMEOUT,
        interval: int = DEFAULT_POLL_INTERVAL,
    ) -> Dict[str, Any]:
        """
        Poll a job until it reaches a terminal state or timeout.

        Terminal states: Successful, Faulted, Canceled, Stopped.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                job = self.get_job_status(job_id)
            except UiPathAPIError as e:
                logger.warning(f"Job status poll failed: {e}")
            else:
                state = job.get("State") or job.get("Status")
                if state:
                    logger.info(f"Job {job_id} state: {state}")
                    if state in ("Successful", "Faulted", "Canceled", "Stopped", "successful", "faulted"):
                        return job
            time.sleep(interval)

        raise UiPathAPIError(f"Job {job_id} did not complete within {timeout}s timeout")

    def execute_process(
        self,
        input_arguments: Optional[Dict[str, Any]] = None,
        timeout: int = DEFAULT_POLL_TIMEOUT,
    ) -> Dict[str, Any]:
        """
        Full execution flow: authenticate → resolve folder → resolve release → start job → poll.

        Returns the final job object from UiPath.
        """
        if not self.is_configured():
            raise UiPathAuthError(
                "UiPath credentials not configured. "
                "Set UIPATH_PAT or UIPATH_CLIENT_ID + UIPATH_CLIENT_SECRET + UIPATH_TENANT_NAME."
            )

        job_id = self.start_job(input_arguments)
        final_state = self.poll_job(job_id, timeout=timeout)
        return final_state
