#!/bin/bash
# ST's DA/provisioning.sh minus its mass erase:
#    Open -> write DA_Config.obk -> iROT-Provisioned or Closed.
set -e
cd "$(dirname "$0")"

case "$1" in
    provisioned) final_state=0x2E ;;
    closed)      final_state=0x72 ;;
    "")          final_state="" ;;
    *)
        echo "usage: $0 [provisioned|closed]"
        echo "No argument: only check that DA_Config.obk suits the connected chip."
        echo "Do 'provisioned' first and prove an unlock + regression before ever using 'closed'."
        exit 1
        ;;
esac

# ST's prebuilt sample OBK (DoEncryption=1, suits H533 but NOT H563)
obk="../DA/Binary/DA_Config.obk"

if [ ! -f "$obk" ]; then
    echo "ST OBK missing at $obk."
    exit 1
fi

# Wrong encrypt flag bricks the board for good (RM0481 p.301), so read the chip,
# not a label, to decide which one it needs
dbgmcu_idcode=0x44024000 # chip identity register: DEV_ID = bits 11:0 (RM0481 p.3119)
rcc_ahb2enr=0x54020C8C   # RCC_AHB2ENR (clock enables), secure alias (RM0481 p.518)
saes_sr=0x520C0C04       # SAES_SR (SAES status register), secure alias
rng_clock=0x00040000     # RNGEN, bit 18 of RCC_AHB2ENR
saes_clock=0x00100000    # SAESEN, bit 20 of RCC_AHB2ENR
cli_reset="STM32_Programmer_CLI -c port=SWD mode=UR"
strip_colors='s/\x1b\[[0-9;]*m//g'

# Prints the word the programmer read at an address, out of its captured output
word_at() { echo "$1" | grep -i "^$2 :" | awk '{print $3}'; }

ahb2enr_reset=$(word_at "$($cli_reset -r32 $rcc_ahb2enr 1 2>&1 | sed "$strip_colors")" $rcc_ahb2enr)
clocks_on=$(printf '0x%08X' $((0x${ahb2enr_reset:-0} | rng_clock | saes_clock)))

# Clocked SAES sits BUSY waiting on the unclocked RNG (RM0481 p.1311); no SAES reads 0 (H573 SR = 0x8, H563 0x0)
# All in one connection: reconnecting under reset would turn the SAES clock off again
probe=$($cli_reset \
    -r32 $dbgmcu_idcode 1 \
    -w32 $rcc_ahb2enr "$clocks_on" \
    -r32 $rcc_ahb2enr 1 \
    -r32 $saes_sr 1 \
    2>&1 | sed "$strip_colors")
idcode=$(word_at "$probe" $dbgmcu_idcode)
ahb2enr=$(word_at "$probe" $rcc_ahb2enr)
saes=$(word_at "$probe" $saes_sr)

# RNG is on every H5, so its clock bit reading back proves the probe itself worked
if [ -z "$idcode" ] || [ -z "$saes" ] || [ $((0x${ahb2enr:-0} & rng_clock)) = 0 ]; then
    echo "Couldn't probe the chip (IDCODE='$idcode' AHB2ENR='$ahb2enr' SAES_SR='$saes'). Is it in Open?"
    exit 1
fi
dev_id=$(printf '0x%03X' $((0x$idcode & 0xFFF)))

# Only exact SAES_SR values count; anything else could be SAES that already finished, so refuse
case "$dev_id/$saes" in
    0x478/00000008) chip="STM32H533";      needs_encrypt=1 ;;
    0x478/00000000) chip="STM32H523";      needs_encrypt=0 ;;
    0x484/00000008) chip="STM32H573";      needs_encrypt=1 ;;
    0x484/00000000) chip="STM32H562/H563"; needs_encrypt=0 ;;
    *)
        echo "Chip ID $dev_id with SAES_SR 0x$saes isn't an RM0481 part this script knows. Refusing."
        exit 1
        ;;
esac
obk_encrypt=$(od -An -tu1 -j8 -N1 "$obk" | tr -d ' ')
echo "$chip (ID $dev_id, SAES_SR 0x$saes) needs DoEncryption=$needs_encrypt; DA_Config.obk has $obk_encrypt."
if [ "$obk_encrypt" != "$needs_encrypt" ]; then
    echo "Mismatch. Set <DoEncryption>$needs_encrypt</DoEncryption> in DA_Config.xml and rerun make_obk.py."
    exit 1
fi
if [ -z "$final_state" ]; then
    echo "DA_Config.obk <DoEncryption> setting fits this chip. Nothing provisioned."
    exit 0
fi

cli="STM32_Programmer_CLI -c port=SWD speed=fast ap=1 mode=Hotplug"
option_bytes=$(STM32_Programmer_CLI -c port=SWD mode=UR -ob displ 2>&1 | sed "$strip_colors")
product_state=$(echo "$option_bytes" | grep -oE 'PRODUCT_STATE\s*:\s*0x[0-9A-Fa-f]+' | grep -oE '0x[0-9A-Fa-f]+' || true)
boot_ube=$(echo "$option_bytes" | grep -oE 'BOOT_UBE\s*:\s*0x[0-9A-Fa-f]+' | grep -oE '0x[0-9A-Fa-f]+' || true)

if [ "$((product_state))" != "$((0xED))" ]; then
    echo "PRODUCT_STATE is ${product_state:-unreadable}, expected 0xED (Open)."
    exit 1
fi
# No STiRoT in this project: after Open the chip must keep booting straight into our secure image
if [ "$((boot_ube))" != "$((0xB4))" ]; then
    echo "BOOT_UBE is $boot_ube, expected 0xB4."
    exit 1
fi
# TZEN must be enabled before provisioning, so that DA can use certs instead of passwords
python3 check_option_bytes.py --pre-provision \
    || { echo "Fix the option bytes above, then rerun provision.sh."; exit 1; }

python3 make_certs.py \
    --check ../DA/Certificates/cert_leaf_chain.b64 > /dev/null \
    || { echo "make_certs.py --check failed."; exit 1; }
python3 make_obk.py \
    --check "$obk" \
    --root-public-key ../DA/Keys/key_1_root_pub.pem > /dev/null \
    || { echo "make_obk.py --check failed."; exit 1; }

printf '%s\n' \
    "About to move this board from Open to PRODUCT_STATE=$final_state." \
    "Getting back to Open needs a working DA unlock, and wipes the board." \
    "Check before going on:" \
    "  - both images are flashed and boot" \
    "  - ST's DA keys, certs and OBK under ../DA are present"
read -r -p "Type 'yes' to continue: " answer
if [ "$answer" != "yes" ]; then
    exit 1
fi

# Provisioning state: only ST's ROM runs, which is what accepts the .obk write
$cli -ob PRODUCT_STATE=0x17
$cli -hardRst
$cli -sdp "$obk"
$cli -hardRst

# The board drops the connection when it changes state, so this can report an error even when it worked
$cli -ob PRODUCT_STATE=$final_state || true
echo
echo "provision.sh is done."
echo "Don't mind the \"Error: ... failed ... \" messages above."
echo "Check with: ./da.sh discover"
