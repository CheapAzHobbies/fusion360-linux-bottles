#!/usr/bin/env bash
# Applies the Wine registry changes that stop the debugger popups.
# See README §5. Run with the bottle shut down.
set -euo pipefail

BOTTLE="${BOTTLE:-$HOME/.var/app/com.usebottles.bottles/data/bottles/bottles/Fusion360}"
WINE="flatpak run --command=/app/bin/wine --env=WINEPREFIX=$BOTTLE com.usebottles.bottles"

[[ -d "$BOTTLE" ]] || { echo "No bottle at $BOTTLE" >&2; exit 1; }

# Auto="1" means "never prompt". Auto="0" is what produces the
# "do you want to debug?" dialog - the opposite of what the name suggests.
for view in 64 32; do
    $WINE reg add 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug' \
        /v Auto /t REG_SZ /d 1 /reg:$view /f
    $WINE reg add 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug' \
        /v Debugger /t REG_SZ /d "" /reg:$view /f
done

# Stops winedbg drawing its crash GUI if it does get launched.
$WINE reg add 'HKCU\Software\Wine\WineDbg' /v ShowCrashDialog /t REG_DWORD /d 0 /f

# Registry writes are buffered; kill the server to flush them to disk.
flatpak run --command=/app/bin/wineserver --env=WINEPREFIX="$BOTTLE" com.usebottles.bottles -k || true
sleep 2

echo
echo "Applied. Verify:"
grep -a -A4 'CurrentVersion\\\\AeDebug' "$BOTTLE/system.reg" | grep '"Auto"'
grep -a 'ShowCrashDialog' "$BOTTLE/user.reg"
