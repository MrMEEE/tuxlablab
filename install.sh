#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DEFAULT_DB_PATH="${XDG_DATA_HOME:-$HOME/.local/share}/tuxlablab/tuxlablab.db"
DB_PATH="${TUXLABLAB_DB:-$DEFAULT_DB_PATH}"
export TUXLABLAB_DB="$DB_PATH"

DB_DIR="$(dirname "$DB_PATH")"
VENV_DIR="$DB_DIR/venv"

printf "Using database: %s\n" "$TUXLABLAB_DB"
printf "Using virtualenv: %s\n" "$VENV_DIR"

mkdir -p "$DB_DIR"
python3 -m venv "$VENV_DIR"

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/pip" install -e "$SCRIPT_DIR"

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
  "$VENV_DIR/bin/tuxlablab" service-install \
    --db-path "$TUXLABLAB_DB" \
    --python "$VENV_DIR/bin/python"
  printf "Service installed with venv Python: %s\n" "$VENV_DIR/bin/python"
else
  printf "Skipped service installation.\n"
fi

printf "\nDone.\n"
printf "Activate venv with: source %s/bin/activate\n" "$VENV_DIR"
printf "Run CLI with: %s/bin/tuxlablab\n" "$VENV_DIR"
printf "Shortcut created: %s\n" "$LAUNCHER"
printf "DB path: %s\n" "$TUXLABLAB_DB"
