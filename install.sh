#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DEFAULT_DB_PATH="${XDG_DATA_HOME:-$HOME/.local/share}/tuxlablab/tuxlablab.db"
DB_PATH="${TUXLABLAB_DB:-$DEFAULT_DB_PATH}"
export TUXLABLAB_DB="$DB_PATH"

DB_DIR="$(dirname "$DB_PATH")"
VENV_DIR="$DB_DIR/venv"
PYTHON_BIN="$VENV_DIR/bin/python"
PIP_BIN="$VENV_DIR/bin/pip"
CLI_BIN="$VENV_DIR/bin/tuxlablab"

printf "Using database: %s\n" "$TUXLABLAB_DB"
printf "Using virtualenv: %s\n" "$VENV_DIR"

mkdir -p "$DB_DIR"

if [[ -x "$PYTHON_BIN" ]]; then
  printf "Existing virtualenv detected, reusing it.\n"
else
  printf "Creating virtualenv...\n"
  python3 -m venv "$VENV_DIR"
fi

if [[ -x "$CLI_BIN" ]]; then
  printf "Existing tuxlablab install detected, updating...\n"
else
  printf "Installing tuxlablab...\n"
fi

"$PYTHON_BIN" -m pip install --upgrade pip
"$PIP_BIN" install --upgrade -e "$SCRIPT_DIR"

if command -v systemctl >/dev/null 2>&1; then
  if [[ -f "$HOME/.config/systemd/user/tuxlablab.service" ]]; then
    printf "Detected existing user service tuxlablab.service; reloading and restarting it...\n"
    if systemctl --user daemon-reload >/dev/null 2>&1; then
      if systemctl --user is-active --quiet tuxlablab.service; then
        if systemctl --user restart tuxlablab.service; then
          printf "Restarted user service tuxlablab.service\n"
        else
          printf "Warning: failed to restart user service tuxlablab.service\n" >&2
        fi
      else
        if systemctl --user is-enabled --quiet tuxlablab.service; then
          if systemctl --user start tuxlablab.service; then
            printf "Started enabled user service tuxlablab.service\n"
          else
            printf "Warning: failed to start enabled user service tuxlablab.service\n" >&2
          fi
        fi
      fi
    else
      printf "Warning: could not access user systemd to reload/restart service in this session\n" >&2
    fi
  fi
fi

BIN_DIR="$HOME/bin"
LAUNCHER="$BIN_DIR/tuxlablab"
mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "$VENV_DIR/bin/activate"
exec tuxlablab "\$@"
EOF
chmod +x "$LAUNCHER"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  printf "Note: %s is not currently in PATH. Add this to your shell profile:\n" "$BIN_DIR"
  printf "  export PATH=\"%s:\$PATH\"\n" "$BIN_DIR"
fi

printf "\nInstall and start user systemd service now? [y/N]: "
read -r reply
if [[ "$reply" =~ ^[Yy]$ ]]; then
  "$CLI_BIN" service-install \
    --db-path "$TUXLABLAB_DB" \
    --python "$PYTHON_BIN"
  printf "Service installed with venv Python: %s\n" "$PYTHON_BIN"
else
  printf "Skipped service installation.\n"
fi

printf "\nDone.\n"
printf "Activate venv with: source %s/bin/activate\n" "$VENV_DIR"
printf "Run CLI with: %s/bin/tuxlablab\n" "$VENV_DIR"
printf "Shortcut created: %s\n" "$LAUNCHER"
printf "DB path: %s\n" "$TUXLABLAB_DB"
