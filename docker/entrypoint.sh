#!/bin/sh
# gui.server binds 127.0.0.1, which a published port can't reach.
socat TCP-LISTEN:8766,fork,reuseaddr TCP:127.0.0.1:8765 &

if [ -f /vpn/IR1-split.ovpn ] && [ -f /vpn/auth.txt ]; then
    # The profile has route-nopull: only its own routes use the tunnel, so the published port keeps working.
    # OpenVPN 2.6 no longer accepts the profile's AES-128-CBC unless it is listed.
    openvpn --config /vpn/IR1-split.ovpn --auth-user-pass /vpn/auth.txt --disable-dco \
        --data-ciphers AES-256-GCM:AES-128-GCM:AES-128-CBC --data-ciphers-fallback AES-128-CBC &
else
    echo "no vpn/IR1-split.ovpn and vpn/auth.txt: starting without the VPN, scrapes may not reach rrk.ir"
fi

exec python3 -m gui.server --db /data/rrk.db
