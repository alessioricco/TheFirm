#!/usr/bin/env bash
# Install the paper-run systemd user unit on a Linux box (see deploy/README.md).
#
#   scripts/install_paper_service.sh <name>     e.g. crypto
#
# Writes ~/.config/systemd/user/firm-paper@.service (this checkout's path and
# uv filled in) and, if missing, ~/.config/firm/paper-<name>.conf from the
# example (mode 600). Starts nothing: that stays a manual step.
set -euo pipefail

name="${1:?usage: $0 <name>   (e.g. crypto)}"
case "$name" in *[!a-z0-9_-]*) echo "name: lowercase letters, digits, - and _ only" >&2; exit 2 ;; esac
[ "$(uname -s)" = Linux ] || { echo "systemd units are for the Linux box" >&2; exit 2; }
command -v systemctl >/dev/null || { echo "no systemctl" >&2; exit 2; }
uv="$(command -v uv)" || { echo "uv not on PATH" >&2; exit 2; }

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
conf_dir="$HOME/.config/firm"
conf="$conf_dir/paper-$name.conf"

mkdir -p "$units" "$conf_dir"
sed -e "s|@REPO@|$repo|g" -e "s|@UV@|$uv|g" \
    "$repo/deploy/systemd/firm-paper@.service" > "$units/firm-paper@.service"
if [ ! -e "$conf" ]; then
    (umask 077; cp "$repo/deploy/paper-run.conf.example" "$conf")
    echo "wrote $conf: fill it in before starting"
else
    echo "kept $conf"
fi
systemctl --user daemon-reload
echo "installed $units/firm-paper@.service"
cat <<NEXT

Next (deploy/README.md):
  1. edit $conf
  2. try it in the foreground for a few minutes:
       set -a; . $conf; set +a; cd $repo
       uv run firm paper run --strategy "\$FIRM_STRATEGY" --verdict "\$FIRM_VERDICT" \\
         --market "\$FIRM_MARKET" --venue "\$FIRM_VENUE" --instrument "\$FIRM_INSTRUMENT" \\
         --tf "\$FIRM_TF" --hours 0.1
  3. sudo loginctl enable-linger $USER     # keep running when logged out
  4. systemctl --user enable --now firm-paper@$name
  5. journalctl --user -u firm-paper@$name -f
NEXT
