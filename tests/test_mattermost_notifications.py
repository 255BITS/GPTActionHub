from shared import notifications

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch


class WebhookHandler(BaseHTTPRequestHandler):
    status = 200
    messages = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.messages.append((self.path, self.headers.get("Content-Type"), json.loads(body)))
        self.send_response(self.status)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class WebhookTests(unittest.TestCase):
    def setUp(self):
        WebhookHandler.status = 200
        WebhookHandler.messages = []
        self.endpoint = ThreadingHTTPServer(("127.0.0.1", 0), WebhookHandler)
        self.thread = threading.Thread(target=self.endpoint.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.endpoint.server_port}/hooks/private-test-key"
        self.addCleanup(self.endpoint.server_close)
        self.addCleanup(self.endpoint.shutdown)

    def test_signup_text_reaches_mattermost_webhook(self):
        with patch.dict("os.environ", {"MATTERMOST_WEBHOOK_URL": self.url}, clear=True):
            self.assertEqual(notifications.send_mattermost_notification("New signup: alice@example.com @channel @martyn"), 200)
        self.assertEqual(WebhookHandler.messages, [
            ("/hooks/private-test-key", "application/json", {"text": "New signup: alice@example.com @\u200bchannel @\u200bmartyn"})
        ])

    def test_old_slack_setting_cannot_send_notifications(self):
        with patch.dict("os.environ", {"SLACK_WEBHOOK_URL": self.url}, clear=True):
            self.assertIsNone(notifications.send_mattermost_notification("disabled"))
        self.assertEqual(WebhookHandler.messages, [])

    def test_http_failure_is_visible_without_secret_url(self):
        WebhookHandler.status = 503
        with patch.dict("os.environ", {"MATTERMOST_WEBHOOK_URL": self.url}, clear=True):
            with self.assertLogs(notifications.logger, level="WARNING") as logs:
                self.assertEqual(notifications.send_mattermost_notification("test"), 503)
        self.assertIn("503", str(logs.output))
        self.assertNotIn("private-test-key", str(logs.output))

    def test_network_failure_does_not_break_signup_or_expose_webhook(self):
        error = notifications.requests.ConnectionError(self.url)
        with patch.dict("os.environ", {"MATTERMOST_WEBHOOK_URL": self.url}, clear=True):
            with patch.object(notifications.requests, "post", side_effect=error):
                with self.assertLogs(notifications.logger, level="WARNING") as logs:
                    self.assertIsNone(notifications.send_mattermost_notification("test"))
        self.assertNotIn("private-test-key", str(logs.output))
