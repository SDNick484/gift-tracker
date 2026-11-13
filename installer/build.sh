#!/bin/sh
# Build dist/setup-tracker.sh: installer/setup-head.sh, a line __PAYLOAD_BELOW__, then the app as a base64 tarball.
#   sh installer/build.sh
# Household defaults come from deploy/deploy.env (git-ignored; see deploy/deploy.env.example). Without it nothing
# is baked in. DEPLOY_ENV=<file> reads another file.
# Every top-level *.py ships, found by glob, so a new module needs no edit here. static/ ships when it exists.
set -eu
cd "$(dirname "$0")/.."
ENV_FILE=${DEPLOY_ENV:-deploy/deploy.env}
STAGE=$(mktemp -d /tmp/tracker-stage.XXXXXX 2>/dev/null || mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p dist "$STAGE/tracker"

for f in *.py; do
  [ -f "$f" ] || { echo "No top-level *.py files found."; exit 1; }
  cp "$f" "$STAGE/tracker/"
done
[ -d static ] && cp -r static "$STAGE/tracker/static"
[ -f ops/tracker-backup ] || { echo "ops/tracker-backup is missing."; exit 1; }
mkdir -p "$STAGE/tracker/ops" && cp ops/tracker-backup "$STAGE/tracker/ops/"

# Clean cache directories portably (avoid find -exec + which BusyBox find can reject)
find "$STAGE" \( -name __pycache__ -o -name .pytest_cache \) -exec rm -rf {} \; 2>/dev/null || true

# Household defaults: ': "${KEY:=value}"' lines, so a variable in the environment still wins.
DEFAULTS="$STAGE/defaults"
: > "$DEFAULTS"
if [ -f "$ENV_FILE" ]; then
  for key in ALLOWED_EMAILS USERS APP_NAME CF_TEAM_DOMAIN CF_AUD MODE; do
    val=$(sed -n "s/^$key=//p" "$ENV_FILE" | head -n1 | sed "s/^[\"']//;s/[\"']\$//")
    if printf '%s' "$val" | grep -q '["`$\\]'; then
      echo "$key in $ENV_FILE contains a character that is not allowed: double quote, backtick, dollar sign or backslash."; exit 1
    fi
    # shellcheck disable=SC2016  # the ${...} is meant for the installer, not for this shell
    [ -n "$val" ] && printf ': "${%s:=%s}"\n' "$key" "$val" >> "$DEFAULTS"
  done
else
  echo "note: $ENV_FILE not found; no household defaults are baked in (copy deploy/deploy.env.example)." >&2
fi

# Check for GNU tar options (--owner/--group) which BusyBox tar does not support
TAR_EXTRA=""
if tar --owner=0 --group=0 --help >/dev/null 2>&1; then
  TAR_EXTRA="--owner=0 --group=0"
fi

OUT=dist/setup-tracker.sh
{
  sed "/^# __DEPLOY_DEFAULTS__\$/r $DEFAULTS" installer/setup-head.sh
  echo "__PAYLOAD_BELOW__"
  # shellcheck disable=SC2086  # TAR_EXTRA is empty or two flags: it must word-split
  ( cd "$STAGE" && tar $TAR_EXTRA -czf - tracker ) | base64
} > "$OUT"
chmod +x "$OUT"
sh -n "$OUT"
echo "Built $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
