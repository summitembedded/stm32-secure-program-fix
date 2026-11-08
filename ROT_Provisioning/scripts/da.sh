#!/bin/bash
# Day-to-day debug authentication with the dev leaf certificate.
set -e
cd "$(dirname "$0")"

cli="STM32_Programmer_CLI -c port=SWD speed=fast"
credentials="key=../DA/Keys/key_3_leaf.pem cert=../DA/Certificates/cert_leaf_chain.b64"

secure_flash=0x0C000000
nonsecure_app=0x08040000
flash_optsr_cur=0x40022050 # flash option status register
strip_colors='s/\x1b\[[0-9;]*m//g'

# Prints one word read over hot-plug SWD, or nothing if the read is blocked
read32() {
    $cli ap=1 mode=Hotplug -r32 "$1" 1 2>&1 | sed "$strip_colors" | grep "^$1 : " | cut -d' ' -f3
}

# Open has no debug auth: debug is always on and certificates are never checked
exit_if_open() {
    optsr=$(read32 $flash_optsr_cur)
    if [ "${optsr:4:2}" = ED ]; then
        echo "Open board: debug is always on, nothing to $1."
        exit 0
    fi
}

# A busy ST-LINK gives DEV_CONNECT_ERR, unlike a locked board's "cannot connect" to the AP
require_probe() {
    if $cli ap=1 mode=Hotplug 2>&1 | sed "$strip_colors" | grep -q "DEV_CONNECT_ERR"; then
        echo "another debug session may already be using the debugger (ST-LINK busy)." >&2
        echo "Close STM32CubeProgrammer or stop any VS Code debug session, then retry." >&2
        exit 1
    fi
}

# Fail hardware commands up front with a clear message if the probe is held
case "$1" in
    discover|unlock|ensure|close|regress) require_probe ;;
esac

case "$1" in
    discover)
        # Shows the product state and the permission letters (H533):
        #   a = full regression       b = regression to TZ-Closed
        #   c/d/e = HDPL3/2/1 secure debug
        #   f/g/h = HDPL3/2/1 non-secure debug
        $cli debugauth=2
        ;;
    unlock)
        if [ -z "$2" ]; then
            exec "$0" help
        fi
        exit_if_open unlock
        # A second unlock without closing first times out, so do what ST's dbg_auth.sh does
        $cli ap=1 mode=Hotplug debugauth=3 > /dev/null || true
        $cli $credentials per="$2" debugauth=1
        echo "Attach with mode=Hotplug ap=1. Connect-under-reset can't attach: debug stays shut until boot leaves the ROM."
        ;;
    ensure)
        # Open boards, and boards already unlocked since power-on, read both 
        # flash aliases; locked non-secure reads as 0
        secure=$(read32 $secure_flash)
        nonsecure=$(read32 $nonsecure_app)
        if [ -n "$secure" ] && [ -n "$nonsecure" ] && [ "$nonsecure" != 00000000 ]; then
            echo "Debug already open."
        else
            "$0" unlock "${2:-eh}"
        fi
        ;;
    close)
        exit_if_open close
        # debugauth=3 isn't in the CLI help, but ST's dbg_auth.sh uses it to close debug
        $cli ap=1 mode=Hotplug debugauth=3
        ;;
    regress)
        exit_if_open regress
        read -r -p "Full regression: mass erase, back to Open, new device keypair. Type 'yes': " answer
        if [ "$answer" != "yes" ]; then
            exit 1
        fi
        # 'a' = full regression (bit 14), per discovery on the H533
        $cli $credentials per="${2:-a}" debugauth=1
        ;;
    *)
        printf '%s
' \
            "usage: $(basename "$0") <command>" \
            "  discover            product state + permission letters" \
            "  unlock <letters>    open debug until power-off, e.g. unlock eh" \
            "  ensure [letters]    unlock only if debug is closed (default eh)" \
            "  close               close debug again" \
            "  regress [letter]    full regression to Open, wipes the board (default a)" \
            "" \
            "letters (H533, join several: eh, cf):" \
            "  a  full regression to Open      b  regression to TZ-Closed" \
            "  c  HDPL3 secure debug           f  HDPL3 non-secure debug" \
            "  d  HDPL2 secure debug           g  HDPL2 non-secure debug" \
            "  e  HDPL1 secure debug           h  HDPL1 non-secure debug" \
            "" \
            "This bootloader-less app runs at HDPL1, so it needs eh." \
            "" \
            "examples:" \
            "  da.sh unlock eh     full debug, secure + non-secure, for this HDPL1 app" \
            "  da.sh regress       back to Open: mass erase, new device keypair, asks for 'yes'"
        exit 1
        ;;
esac
