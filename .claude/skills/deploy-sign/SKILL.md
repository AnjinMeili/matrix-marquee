---
name: deploy-sign
description: Deploy this project to the protest sign (the Raspberry Pi at protest.local) and verify it on the hardware. Use whenever the owner says "deploy", asks to put a change on the sign, roll back a release, or check that the sign is healthy after a change, a reboot, or a power cycle. Covers the explicit-approval rule, the database backup before schema changes, the deploy itself, the read-only verification script, exercising the new feature on the real panel, and rollback.
---

# Deploying to the sign

The sign is a Raspberry Pi 4 on DietPi, reachable as `protest.local` over SSH as
root. A deploy restarts the service, so the panel blanks for a few seconds and
then resumes the playlist where it left off. Bugs that never showed in tests
have shown up on the real sign: frozen iPhone video, a media list returning
errors, previews too slow over Wi-Fi. So a deploy is not done until it has been
checked on the hardware.

## Rules

- **Deploy only on an explicit "deploy" from the owner.** Commit and push are
  separate go-aheads; never fold them in. Claude Code's auto mode blocks
  deploys and remote writes without that go-ahead, so ask instead of retrying.
- **Never leave the owner's content changed.** Verification must not edit
  their tags, ratings, playlists, or display settings. If a check has to
  change something (for example moving the brightness slider), restore the
  previous value and say so.
- Use `protest.local`, never an IP address, in anything written down.

## Steps

1. **Check before deploying.** Run `python -m pytest`, which includes the UI
   check when Chrome is available. If the change touches the web UI, also look
   at the `ui-check` screenshots.
2. **Back up the database if the schema changed.** Compare the deployed
   schema with the working tree:

   ```sh
   ssh root@protest.local cat /opt/protest/current/src/matrix_player/database.py | diff -q - src/matrix_player/database.py
   ```

   If they differ (new tables, columns, or migrations), take a consistent copy
   first:

   ```sh
   ssh root@protest.local 'stamp=$(date -u +%Y%m%dT%H%M%SZ); python3 -c "import sqlite3,sys; s=sqlite3.connect(\"/var/lib/protest/matrix-player.sqlite3\"); d=sqlite3.connect(sys.argv[1]); s.backup(d); d.close()" /var/backups/protest/matrix-player-$stamp.sqlite3 && chmod 0600 /var/backups/protest/matrix-player-$stamp.sqlite3 && echo backed up $stamp'
   ```

3. **Deploy:** `scripts/deploy.sh --skip-os-packages`. Leave out
   `--skip-os-packages` when `scripts/install.sh` changed its apt package list
   (it needs the Pi to have internet).
4. **Verify:** `scripts/verify-deploy.sh`. It is read-only and exits non-zero
   on any failure. It checks:
   - every page and API answers 200;
   - the service is active with no restarts and no errors in the journal;
   - the sign is playing with no skipped items;
   - no under-voltage or slow-down since boot.
5. **Exercise the new feature on the sign.** Measure what it was for: time
   playlist items through `/api/playback/state`, read `/api/usage` or
   `/api/health`, fetch a preview and time it, or read a frame from
   `/api/playback/frame`. For anything visual, the owner has to look at the
   panel; say what to look for.
6. **Report briefly:** the release, what was checked, what was measured, and
   any backup made. Tell the owner to reload the page on the phone, since the
   iPhone captive-portal window caches it.

## Rollback

```sh
ssh root@protest.local 'ls -1 /opt/protest/releases | tail -5'
ssh root@protest.local 'ln -sfn /opt/protest/releases/<previous> /opt/protest/current && systemctl restart protest'
scripts/verify-deploy.sh
```

Database changes are additive, so an older release runs against a newer
database. If a database backup must be restored instead, stop the service,
copy the backup over `/var/lib/protest/matrix-player.sqlite3`, and start it
again.

## Shell gotchas (the operator's machine runs zsh)

- Never name a loop variable `path`; in zsh it overwrites `PATH` and every
  command after it is "not found".
- `set -- $var` does not split words in zsh. Build JSON request bodies with
  `python3 -c` instead.
- Polling with a bare `sleep` is blocked in Claude Code; use an
  `until <check>; do sleep 2; done` loop.
