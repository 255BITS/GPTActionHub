# Mattermost notification release

At release, create a private incoming webhook bound to `255labs/#events` on
`https://chat.255labs.xyz`. Put its URL in `MATTERMOST_WEBHOOK_URL` in
`/etc/gptactionhub/portal.env` (root:gptactionhub, existing permissions). Remove
`SLACK_WEBHOOK_URL`. `/etc/gptactionhub/proxy.env` currently has no notification
webhook; leave `MATTERMOST_WEBHOOK_URL` unset there to preserve that behavior, or
configure a separate `#internal` webhook when startup alerts are wanted.

Merge through the normal reviewed `main` workflow, which deploys `gptactionhub`
and restarts its units. Verify `/login`, existing API actions, and one controlled
signup; confirm the notification appears once in Mattermost and no Slack post
appears. Notification HTTP/network failures are best effort with a 10-second
bound and do not fail signup or prevent the proxy's intended shutdown.

Rollback: revert this PR via the same release workflow, restore the saved
`SLACK_WEBHOOK_URL` in the portal env, and restart the affected units. Keep the
old credential private until the release is accepted, then revoke it. No database
migration is required. The old Swarm manifest uses the new env key too; it is
historical deployment support, not a second runtime to release.

Validation: `python -m unittest discover -s tests -p test_mattermost_notifications.py -v`
uses a loopback HTTP receiver and covers JSON delivery, disabled notifications,
HTTP failure reporting, and redaction of webhook secrets on network failure.

User-entered notification text neutralizes direct/channel mentions with a zero-width
separator after standalone `@` characters; ordinary email addresses remain intact.
