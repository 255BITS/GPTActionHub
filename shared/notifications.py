"""Optional internal notifications to the self-hosted Mattermost webhook."""

import logging
import os

import requests

logger = logging.getLogger(__name__)


def send_mattermost_notification(message):
    webhook_url = os.getenv("MATTERMOST_WEBHOOK_URL")
    if not webhook_url:
        return None
    try:
        response = requests.post(webhook_url, json={"text": message}, timeout=10)
    except requests.RequestException:
        # Request errors can contain the secret webhook URL.
        logger.warning("Mattermost notification could not be delivered")
        return None
    if response.status_code >= 400:
        logger.warning("Mattermost notification failed (HTTP %s)", response.status_code)
    return response.status_code
