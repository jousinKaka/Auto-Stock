#!/usr/bin/env bash
set -euo pipefail

expected_dir="/home/ubuntu/auto-stock"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$(id -un)" != "ubuntu" ]]; then
    echo "Run this installer as the ubuntu user, not as root." >&2
    exit 1
fi

if [[ "$repo_dir" != "$expected_dir" ]]; then
    echo "Clone the repository to $expected_dir before running this installer." >&2
    exit 1
fi

if ! sudo test -s /etc/auto-stock/auto-stock.env; then
    echo "Create /etc/auto-stock/auto-stock.env before installing the timers." >&2
    exit 1
fi

python_bin=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        if [[ "$(printf '%s\n' 3.10 "$version" | sort -V | head -n 1)" == "3.10" ]]; then
            python_bin="$(command -v "$candidate")"
            break
        fi
    fi
done

if [[ -z "$python_bin" ]]; then
    echo "Python 3.10 or newer is required. Install it and rerun this script." >&2
    exit 1
fi

"$python_bin" -m venv "$repo_dir/.venv"
"$repo_dir/.venv/bin/python" -m pip install -r "$repo_dir/requirements.txt"

sudo install -m 0644 "$repo_dir/deploy/systemd/auto-stock@.service" /etc/systemd/system/auto-stock@.service
sudo install -m 0644 "$repo_dir/deploy/systemd/auto-stock-morning.timer" /etc/systemd/system/auto-stock-morning.timer
sudo install -m 0644 "$repo_dir/deploy/systemd/auto-stock-evening.timer" /etc/systemd/system/auto-stock-evening.timer
sudo systemd-analyze verify \
    /etc/systemd/system/auto-stock@.service \
    /etc/systemd/system/auto-stock-morning.timer \
    /etc/systemd/system/auto-stock-evening.timer
sudo systemctl daemon-reload
sudo systemctl enable --now auto-stock-morning.timer auto-stock-evening.timer
sudo systemctl list-timers auto-stock-morning.timer auto-stock-evening.timer
