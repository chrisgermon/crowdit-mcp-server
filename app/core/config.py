import os
import logging
from typing import Optional

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_secret_sync(secret_id: str, timeout_seconds: float = 5.0) -> Optional[str]:
    """Read the latest version of a secret from Google Secret Manager.

    Args:
        secret_id: The ID of the secret to read
        timeout_seconds: Timeout for the Secret Manager API call (default 5 seconds)
    """
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        # Use GCP_PROJECT_ID first (explicitly set in Cloud Run), then GOOGLE_CLOUD_PROJECT, then default
        project_id = os.getenv("GCP_PROJECT_ID", os.getenv("GOOGLE_CLOUD_PROJECT", "crowdmcp"))
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"

        response = client.access_secret_version(
            request={"name": name},
            timeout=timeout_seconds
        )
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        logger.warning(f"Failed to read secret {secret_id} from Secret Manager: {e}")
        return None

def update_secret_sync(secret_id: str, value: str, timeout_seconds: float = 10.0) -> bool:
    """Update a secret in Google Secret Manager (sync version).

    Args:
        secret_id: The ID of the secret to update
        value: The new value for the secret
        timeout_seconds: Timeout for the Secret Manager API call (default 10 seconds)
    """
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        # Use GCP_PROJECT_ID first (explicitly set in Cloud Run), then GOOGLE_CLOUD_PROJECT, then default
        project_id = os.getenv("GCP_PROJECT_ID", os.getenv("GOOGLE_CLOUD_PROJECT", "crowdmcp"))
        parent = f"projects/{project_id}/secrets/{secret_id}"

        client.add_secret_version(
            request={
                "parent": parent,
                "payload": {"data": value.encode("UTF-8")}
            },
            timeout=timeout_seconds
        )
        logger.info(f"Updated secret: {secret_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to update secret {secret_id}: {e}")
        return False
