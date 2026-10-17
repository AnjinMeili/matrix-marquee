#!/bin/sh
set -eu

usage() {
    cat <<'EOF'
Usage: setup-ap.sh --apply --acknowledge-network-change [--config FILE]

Guarded AP setup for DietPi. This changes the selected interface to a static
address and may disconnect it. It refuses to run unless both explicit flags
are supplied. Configure /etc/protest/ap.env first.
EOF
}

AP_ENV=/etc/protest/ap.env
APPLY=0
ACK=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1; shift ;;
        --acknowledge-network-change) ACK=1; shift ;;
        --config) AP_ENV=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
[ "$APPLY" -eq 1 ] && [ "$ACK" -eq 1 ] || { usage >&2; exit 2; }
[ -r "$AP_ENV" ] || { echo "missing AP config: $AP_ENV" >&2; exit 1; }
# shellcheck disable=SC1090
. "$AP_ENV"
: "${AP_INTERFACE:?missing AP_INTERFACE}"
: "${AP_SSID:?missing AP_SSID}"
: "${AP_PASSPHRASE:?missing AP_PASSPHRASE}"
: "${AP_COUNTRY:?missing AP_COUNTRY}"
: "${AP_CHANNEL:?missing AP_CHANNEL}"
: "${AP_ADDRESS:?missing AP_ADDRESS}"
: "${DHCP_START:?missing DHCP_START}"
: "${DHCP_END:?missing DHCP_END}"

case "$AP_INTERFACE" in *[!A-Za-z0-9_.-]*) echo "invalid interface" >&2; exit 2;; esac
case "$AP_SSID" in *[!A-Za-z0-9_.@%+=:-]*) echo "SSID contains unsupported characters" >&2; exit 2;; esac
[ "${#AP_SSID}" -ge 1 ] && [ "${#AP_SSID}" -le 32 ] ||
    { echo "SSID must be 1-32 characters" >&2; exit 2; }
case "$AP_COUNTRY" in [A-Z][A-Z]) :;; *) echo "country must be two uppercase letters" >&2; exit 2;; esac
case "$AP_CHANNEL" in 1|2|3|4|5|6|7|8|9|10|11) :;; *) echo "unsupported 2.4GHz channel" >&2; exit 2;; esac
[ "${#AP_PASSPHRASE}" -ge 8 ] && [ "${#AP_PASSPHRASE}" -le 63 ] ||
    { echo "passphrase must be 8-63 characters" >&2; exit 2; }
case "$AP_PASSPHRASE" in *[!A-Za-z0-9_.@%+=:-]*) echo "passphrase contains unsupported characters" >&2; exit 2;; esac
for address in "$AP_ADDRESS" "$DHCP_START" "$DHCP_END"; do
    printf '%s\n' "$address" | awk -F. '
        NF != 4 { exit 1 }
        { for (i = 1; i <= 4; i++) if ($i !~ /^[0-9]+$/ || $i > 255) exit 1 }
    ' ||
        { echo "invalid IPv4 address: $address" >&2; exit 2; }
done
ap_prefix=${AP_ADDRESS%.*}
[ "${DHCP_START%.*}" = "$ap_prefix" ] && [ "${DHCP_END%.*}" = "$ap_prefix" ] ||
    { echo "DHCP range must be in the AP /24 subnet" >&2; exit 2; }
ip link show "$AP_INTERFACE" >/dev/null 2>&1 || { echo "interface not found" >&2; exit 1; }
if ip route show default | grep -q "dev $AP_INTERFACE"; then
    echo "refusing: $AP_INTERFACE currently carries the default route" >&2
    exit 1
fi
existing_addresses=$(ip -4 -o address show dev "$AP_INTERFACE" | awk '{print $4}')
if [ -n "$existing_addresses" ] && [ "$existing_addresses" != "$AP_ADDRESS/24" ]; then
    echo "refusing: $AP_INTERFACE has a conflicting IPv4 address" >&2
    exit 1
fi
existing_route=$(ip -4 route show "${AP_ADDRESS%.*}.0/24")
if [ -n "$existing_route" ] && ! printf '%s\n' "$existing_route" | grep -q "dev $AP_INTERFACE"; then
    echo "refusing: AP subnet already has a conflicting route" >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TEMPLATE_DIR="$SCRIPT_DIR/../deploy/ap"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="/var/backups/protest/network-$STAMP"
install -d -m 0700 "$BACKUP" /etc/hostapd /etc/dnsmasq.d
for file in /etc/hostapd/hostapd.conf /etc/dnsmasq.d/protest.conf \
    /etc/systemd/system/protest-ap-interface.service; do
    [ ! -e "$file" ] || cp -a "$file" "$BACKUP/"
done

render() {
    sed -e "s|@AP_INTERFACE@|$AP_INTERFACE|g" \
        -e "s|@AP_SSID@|$AP_SSID|g" \
        -e "s|@AP_PASSPHRASE@|$AP_PASSPHRASE|g" \
        -e "s|@AP_COUNTRY@|$AP_COUNTRY|g" \
        -e "s|@AP_CHANNEL@|$AP_CHANNEL|g" \
        -e "s|@AP_ADDRESS@|$AP_ADDRESS|g" \
        -e "s|@DHCP_START@|$DHCP_START|g" \
        -e "s|@DHCP_END@|$DHCP_END|g" "$1" > "$2"
}
install -m 0600 /dev/null /etc/hostapd/hostapd.conf
install -m 0644 /dev/null /etc/dnsmasq.d/protest.conf
render "$TEMPLATE_DIR/hostapd.conf.template" /etc/hostapd/hostapd.conf
render "$TEMPLATE_DIR/dnsmasq.conf.template" /etc/dnsmasq.d/protest.conf
cat > /etc/systemd/system/protest-ap-interface.service <<EOF
[Unit]
Description=Configure dedicated Protest AP interface
Before=hostapd.service dnsmasq.service
After=sys-subsystem-net-devices-${AP_INTERFACE}.device
BindsTo=sys-subsystem-net-devices-${AP_INTERFACE}.device

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/ip address replace ${AP_ADDRESS}/24 dev ${AP_INTERFACE}
ExecStart=/sbin/ip link set ${AP_INTERFACE} up
ExecStop=-/sbin/ip address delete ${AP_ADDRESS}/24 dev ${AP_INTERFACE}

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 /etc/systemd/system/protest-ap-interface.service
command -v hostapd >/dev/null 2>&1 || { echo "hostapd is not installed" >&2; exit 1; }
command -v dnsmasq >/dev/null 2>&1 || { echo "dnsmasq is not installed" >&2; exit 1; }
dnsmasq --test
systemctl daemon-reload
systemctl unmask hostapd
systemctl enable protest-ap-interface.service hostapd dnsmasq
systemctl restart protest-ap-interface.service
systemctl restart hostapd dnsmasq
echo "AP configured on $AP_INTERFACE at $AP_ADDRESS; previous files: $BACKUP"
