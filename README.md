# Protest RGB Matrix Appliance

DietPi appliance software for a Raspberry Pi 4 driving an RGB LED matrix. The
application remains usable offline and can expose its UI through a guarded,
optional Wi-Fi AP/captive portal.

## Install and deploy

The appliance currently runs as root because the matrix driver needs direct
hardware access. The systemd sandbox still restricts filesystem writes to
`/var/lib/protest` and `/var/log/protest`. Review `scripts/install.sh` before
use, then install locally on DietPi:

```sh
sudo scripts/install.sh
```

For auditable, non-interactive deployment from an operator machine:

```sh
scripts/deploy.sh --host 192.168.1.194 --user root
```

The host is configurable by `--host` or `PROTEST_DEPLOY_HOST`. SSH uses batch
mode and strict host-key checking. Deployment never transfers `.env`, runtime
data, media, or credentials. It creates timestamped releases under
`/opt/protest/releases` and atomically updates `/opt/protest/current`.

Copy `.env.example` to `/etc/protest/protest.env`, mode `0600`, and adjust the
application module if needed. The configured viewing area is 96x64. The
hardware driver uses the supplied initialization string, whose raw framebuffer
is 64x96 and is rotated by the configured mapper:

```text
--led-rows=32 --led-cols=64 --led-chain=1 --led-parallel=3
--led-multiplexing=1 --led-pixel-mapper=Rotate:90
--led-pwm-lsb-nanoseconds=160 --led-slowdown-gpio=2
```

Common operations:

```sh
systemctl status protest
journalctl -u protest -f
scripts/health-check.sh
systemctl restart protest
```

## Wi-Fi AP and captive portal

AP setup is intentionally **not** part of installation. Changing the wrong
interface can cut off SSH. First install the required DietPi packages:

```sh
apt-get install hostapd dnsmasq
install -m 0600 deploy/ap/ap.env.example /etc/protest/ap.env
editor /etc/protest/ap.env
```

Use a dedicated Wi-Fi interface that does not carry the default route. The
default AP network is `10.42.0.0/24`; verify it does not overlap another local
network. Only after local/console recovery is available, apply explicitly:

```sh
scripts/setup-ap.sh --apply --acknowledge-network-change
```

The script validates inputs and configuration, refuses an interface with an
IPv4 address/default route or an existing AP-subnet route, backs up replaced
files, and only then installs a narrowly scoped interface unit and enables
`hostapd` and `dnsmasq`. It does not replace DietPi's network manager. Wildcard
DNS points clients to the local portal. The application must serve captive
detection routes and HTTP on the AP address. NAT/upstream sharing is
deliberately not enabled; add it only after an operator has identified and
secured an upstream interface.

To recover, connect locally; disable `hostapd`, `dnsmasq`, and
`protest-ap-interface`; remove their three generated configuration/unit files;
restore files from the reported `/var/backups/protest/network-*` directory;
then reload systemd and restart the prior network manager.

## Logs, backups, and incident data

Journald is persistent, compressed, capped at 256 MiB, and retained up to 30
days. Application file logs under `/var/log/protest` rotate daily for 30
rotations. Application logs must never include credentials, request bodies,
uploaded text/content, or original filenames; use event IDs and media IDs.

Create a configuration-only backup (no secrets, DB, logs, or user content):

```sh
sudo scripts/backup.sh
```

Create a diagnostics bundle with bounded journals and defensive redaction:

```sh
sudo LOG_SINCE='2 hours ago' scripts/log-bundle.sh
```

Both outputs are mode `0600` under `/var/backups/protest`. Review a bundle
before sharing it. Uploaded media, environment files, AP credentials,
databases, and application content are never included.

## Updating and rollback

Re-run `deploy.sh` for an atomic release update. To roll back:

```sh
ln -sfn /opt/protest/releases/<previous-timestamp> /opt/protest/current
systemctl restart protest
```

Before maintenance, run the health check and configuration backup. After an
update, inspect `systemctl status protest`, run `scripts/health-check.sh`, and
confirm the matrix blanks safely when playback stops. Do not expose port 8000
to an untrusted network; the offline AP UI has no assumption of remote access.
