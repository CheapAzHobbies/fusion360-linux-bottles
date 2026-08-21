#!/usr/bin/env bash
# adskidmgr:// scheme handler -> Autodesk Identity Manager in the Bottles Fusion360 prefix.
#
# Autodesk's web sign-in finishes by redirecting the browser to adskidmgr://...
# Something on the host has to catch that URL and hand it to the identity
# manager running inside the same Wine prefix as Fusion, or Fusion sits on
# the login screen forever.
#
# Running via `flatpak run --command=<runner>` (rather than invoking the runner
# directly) is the important part: it joins the Bottles sandbox and therefore
# talks to the wineserver that is already running, instead of starting an
# isolated second one.
#
# The runner is read from bottle.yml rather than hardcoded: using a different
# Wine build than the bottle's gets you
#   wine client error:0: version mismatch <server>/<client>
# and the token is silently dropped.

LOG="$HOME/.local/share/adskidmgr-handler.log"
BOTTLES="$HOME/.var/app/com.usebottles.bottles/data/bottles"
BOTTLE="$BOTTLES/bottles/Fusion360"

echo "$(date '+%F %T') called with: $1" >> "$LOG"

RUNNER_NAME=$(sed -n 's/^Runner:[[:space:]]*//p' "$BOTTLE/bottle.yml" | head -1)
case "$RUNNER_NAME" in
    sys-wine-*|"") RUNNER="/app/bin/wine" ;;                       # Bottles' bundled Wine
    *)             RUNNER="$BOTTLES/runners/$RUNNER_NAME/bin/wine" ;;
esac
echo "$(date '+%F %T') runner: $RUNNER_NAME -> $RUNNER" >> "$LOG"

# Newest AdskIdentityManager.exe in the bottle. Autodesk changes the hashed
# directory name on every update, so this is detected rather than hardcoded.
EXE_UNIX=$(find "$BOTTLE/drive_c" -name AdskIdentityManager.exe -printf '%T@ %p\n' 2>/dev/null \
           | sort -rn | head -1 | cut -d' ' -f2-)

if [[ -z "$EXE_UNIX" ]]; then
    echo "$(date '+%F %T') ERROR: no AdskIdentityManager.exe under $BOTTLE/drive_c" >> "$LOG"
    exit 1
fi

EXE_WIN='C:'"$(printf '%s' "${EXE_UNIX#"$BOTTLE/drive_c"}" | tr '/' '\\')"
echo "$(date '+%F %T') using: $EXE_WIN" >> "$LOG"

flatpak run \
    --command="$RUNNER" \
    --env=WINEPREFIX="$BOTTLE" \
    com.usebottles.bottles \
    "$EXE_WIN" "$1" >> "$LOG" 2>&1

echo "$(date '+%F %T') wine exited $?" >> "$LOG"
