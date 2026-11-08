#!/usr/bin/env python3
"""Flash secure and non-secure ELFs over SWD on a provisioned H5 after a debug
   auth unlock. 
   
   STM32_Programmer_CLI can't do this due to 3 bugs in its implementation where
   it tries to drive secure flash through the non-secure flash controller.
   
   This writes each controller's registers directly."""

import argparse
import os
import sys
import time

# pyocd usually lives in its own pipx env, re-exec there when the import fails
try:
    import pyocd
    import elftools
except ImportError:
    pipx_home = os.environ.get("PIPX_HOME") or os.path.expanduser("~/.local/share/pipx")
    pipx_python = os.path.join(pipx_home, "venvs", "pyocd", "bin", "python")
    if (os.path.abspath(sys.executable) != os.path.abspath(pipx_python)
            and os.path.exists(pipx_python)):
        os.execv(pipx_python, [pipx_python, os.path.abspath(__file__), *sys.argv[1:]])
    sys.exit("missing pyocd and pyelftools. Install with:\n"
             "  pipx install pyocd   (pyelftools comes with it)")

from elftools.elf.elffile import ELFFile
from pyocd.core.helpers import ConnectHelper
from pyocd.core.target import Target
from pyocd.target import TARGET
from pyocd.target.pack.pack_target import ManagedPacks

# Flash controller registers, same on every STM32H5
#  (RM0481 Rev 4 §7.11 pg.318; base addresses pg.120)
CONTROLLERS = {
    "secure": {
        "flash_start": 0x0C000000,
        "unlock":      0x50022008,  # SECKEYR (Secure Key Register, for the flash unlock words), p.319
        "status":      0x50022024,  # SECSR (Secure Status Register): busy + error flags, p.325
        "control":     0x5002202C,  # SECCR (Secure Control Register): lock, erase, program, p.329
        "clear_flags": 0x50022034,  # SECCCR (Secure Clear Control Register), p.333
    },
    "non-secure": {
        "flash_start": 0x08000000,
        "unlock":      0x40022004,  # NSKEYR (Non-Secure Key Register), p.319
        "status":      0x40022020,  # NSSR (Non-Secure Status Register), p.323
        "control":     0x40022028,  # NSCR (Non-Secure Control Register), p.327
        "clear_flags": 0x40022030,  # NSCCR (Non-Secure Clear Control Register), p.332
    },
}


def add_flash_layout(target_name):
    """Fill in each controller's flash range and sector size from the target's
       CMSIS pack."""
    ManagedPacks.populate_target(target_name)
    if target_name not in TARGET:
        sys.exit(f"{target_name}: no installed pack has it. Try 'pyocd pack install {target_name}'.")
    try:
        # _pack_device is pyocd internals (tested on 0.44.1). A future pyocd
        #  version could rename it
        regions = TARGET[target_name]._pack_device.memory_map
    except AttributeError:
        sys.exit(f"pyocd {pyocd.__version__} no longer has _pack_device, which this script uses to read "
                 "the flash layout (worked on 0.44.1). Update add_flash_layout().")

    for c in CONTROLLERS.values():
        region = next((r for r in regions if r.is_flash and r.start == c["flash_start"]), None)
        if region is None or region.flm is None:
            sys.exit(f"{target_name}: pack has no flash at 0x{c['flash_start']:08X}")
        sector_sizes = {size for _, size in region.flm.sector_sizes}
        if len(sector_sizes) != 1:
            sys.exit(f"{target_name}: expected one sector size, pack has {sorted(sector_sizes)}")
        
        c["flash"] = range(region.start, region.start + region.length)
        c["sector_size"] = sector_sizes.pop()
        # Every H5 is dual-bank, split in half
        c["sectors_per_bank"] = region.length // 2 // c["sector_size"]


def load_image(elf_path):
    """Read the parts of the ELF that go into flash, split into the 16-byte 
       chunks flash is written in.

       Returns {address: 16 bytes}. Chunks that are all 0xFF are skipped, 
       since erase already leaves them that way."""
    erased = b"\xff"  # erased flash reads back as all 0xFF
    quads = {}
    with open(elf_path, "rb") as f:
        for segment in ELFFile(f).iter_segments():
            # ELF spec: PT_LOAD  = segment that gets loaded, 
            #           p_filesz = its bytes in the file (0 for RAM-only .bss)
            if segment["p_type"] != "PT_LOAD" or segment["p_filesz"] == 0:
                continue
            # ELF spec: p_paddr is the load address; .data runs from RAM but
            #           its initial values are stored in flash here
            start = segment["p_paddr"]
            if not any(start in c["flash"] for c in CONTROLLERS.values()):
                continue
            data = segment.data()[:segment["p_filesz"]]
            # Flash programs whole 16-byte quad-words, so pad both ends with the erased value
            lead = start % 16
            data = erased * lead + data         # pad start to a multiple of 16
            data += erased * (-len(data) % 16)  # pad end to a multiple of 16
            for offset in range(0, len(data), 16):
                quad = data[offset:offset + 16]
                if quad != erased * 16:  # skip 0xFF quads. Writes are done by offset, so it's ok
                    address = start - lead + offset
                    # If the previous segment ended mid-quad, AND with its part of this quad
                    quads[address] = bytes(a & b for a, b in zip(quads.get(address, erased * 16), quad))
    return quads


def which_flash_controller(address):
    """Registers of the flash controller (secure or non-secure) that owns
      this flash address. 
      This is the core change that resolves the STM32_Programmer_CLI bugs."""
    for controller in CONTROLLERS.values():
        if address in controller["flash"]:
            return controller
    sys.exit(f"0x{address:08X} isn't in flash")


# Status register bits (RM0481 pg.323-327)
SR_BSY          = 1 << 0      # BSY (Busy): an erase or program is running
SR_ERRORS       = 0x00FE0000  # every error flag
CLEAR_ALL_FLAGS = 0x00FF0000  # write clears register: end-of-operation + every error flag


def wait_not_busy(target, regs, what):
    """Wait for the erase or program to finish, and exit if it failed or never finishes."""
    deadline = time.time() + 5  # an 8 KB sector erase or a program finishes
                                # in far less; 5 s means it's stuck
    while time.time() < deadline:
        status = target.read32(regs["status"])  # no sleep: each read is already a ~0.2 ms USB round trip to the ST-LINK
        if not status & SR_BSY:
            if status & SR_ERRORS:
                sys.exit(f"{what}: status 0x{status:08X} has error flags")
            return
    sys.exit(f"{what}: still busy")


# Flash stays write-protected until these two values are written to its unlock
# register, in this order (RM0481 p.319)
FLASH_UNLOCK_WORD1, FLASH_UNLOCK_WORD2 = 0x45670123, 0xCDEF89AB

# Control register bits (RM0481 pg.327-332)
CR_LOCK    = 1 << 0   # LOCK: flash is write-protected
CR_PROGRAM = 1 << 1   # PG (Program): writes to flash get programmed
CR_SER     = 1 << 2   # SER (Sector Erase): erase the sector in SNB
CR_START   = 1 << 5   # STRT (Start): kick off the erase
CR_SNB_POS = 6        # SNB (Sector Number) field starts at bit 6
CR_BKSEL   = 1 << 31  # BKSEL (Bank Select): 0 = bank 1, 1 = bank 2

# RM0481 sector erase (p.257) + single-write (p.255) steps; 
# "ST:" marks where STM32_Programmer_CLI differs
def flash(target, quads):
    # One ELF lives in one world, so its first address picks the controller
    first_address = min(quads)
    regs = which_flash_controller(first_address)  # ST: uses NSxxx registers, even for secure flash
    base = regs["flash"].start
    target.write32(regs["clear_flags"], CLEAR_ALL_FLAGS)  # RM0481 pg.257 step 3

    if target.read32(regs["control"]) & CR_LOCK:  # RM0481 pg.257 step 4, only if locked. 
                                                  # ST: unlocks unconditionally. # Unlocking an already-unlocked
                                                  #     controller bus-faults and locks it until reset
        target.write32(regs["unlock"], FLASH_UNLOCK_WORD1) # RM0481 p.319
        target.write32(regs["unlock"], FLASH_UNLOCK_WORD2) # RM0481 p.319
    if target.read32(regs["control"]) & CR_LOCK:
        sys.exit("flash controller still locked. Is debug unlocked (./da.sh unlock eh)?")

    # ST steps we skip:
    #   ACR wait states: already right for the current clock (reset default, or set by the halted app)
    #   ICACHE invalidate: the NRST pulse at the end clears the cache anyway
    #   SECBB block security: the option-byte watermarks already make the secure sectors secure

    ## ERASE
    sectors_per_bank = regs["sectors_per_bank"]  # from the pack, so a 2 MB H573 gets 128, not 32
    for sector in sorted({(address - base) // regs["sector_size"] for address in quads}):
        bank = CR_BKSEL if sector >= sectors_per_bank else 0
        erase_control = CR_SER | ((sector % sectors_per_bank) << CR_SNB_POS) | bank
        target.write32(regs["control"], erase_control)            # RM0481 pg.257 step 5, same value as ST
        target.write32(regs["control"], erase_control | CR_START) # RM0481 pg.257 step 6, same value as ST
        wait_not_busy(target, regs, f"erase sector {sector}")     # RM0481 pg.257 step 7. ST: ignores error flags and carries on
    target.write32(regs["control"], 0)  # RM0481 pg.257 step 9, clear SER. ST: skips it

    ## PROGRAM
    target.write32(regs["control"], CR_PROGRAM)  # RM0481 pg.255 step 5. ST: writes 0x1, which is LOCK, not PROGRAM
    # Batch consecutive quads into one write, even across sectors; 
    #    a skipped 0xFF quad starts a new batch.
    # No BSY polling per quad: the flash controller holds off our next write on
    #    the chip's AHB bus until the previous quad is done (RM0481 pg.253)
    addresses = sorted(quads)
    batch_start, batch = addresses[0], bytearray()
    for address in addresses:
        if address != batch_start + len(batch):
            target.write_memory_block8(batch_start, batch)
            batch_start, batch = address, bytearray()
        batch += quads[address]
    target.write_memory_block8(batch_start, batch)  # the last batch
    wait_not_busy(target, regs, "program")   # RM0481 pg.255 step 7. ST: polls NSSR, so it never sees secure errors
    target.write32(regs["control"], 0)       # RM0481 pg.255 step 8, clear PROGRAM
    target.write32(regs["control"], CR_LOCK) # relock. ST: leaves the controller unlocked


def verify(target, quads):
    bad = [address for address in sorted(quads)
           if bytes(target.read_memory_block8(address, 16)) != quads[address]]
    return len(quads) - len(bad), len(quads)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "-t", "--target",
    required=True,
    help="pyocd pack target for the flash layout, e.g. stm32h533retx (see 'pyocd list --targets')",
)
parser.add_argument(
    "elf",
    nargs="*",
    help="ELFs to flash, e.g. Secure/build/sec_flash_demo_s.elf "
         "NonSecure/build/sec_flash_demo_ns.elf; none = only reset",
)
parser.add_argument(
    "--halt",
    action="store_true",
    help="pin reset, then hold the core at the secure reset vector for a debugger to attach",
)
parser.add_argument(
    "--no-reset",
    action="store_true",
    help="leave the core halted where it was instead of pulsing NRST at the end",
)
args = parser.parse_args()
add_flash_layout(args.target.lower())

started = time.time()
# pyocd session settings
pyocd_options = {
    "connect_mode": "attach",               # connect-under-reset fails on a provisioned board
    "target_override": "cortex_m",          # plain memory access only; the stm32h533 target brings 
                                            #     its own flash code, which fails with TrustZone on
    "frequency": 8000000,                   # SWD clock, 8 MHz
    "resume_on_disconnect": not args.halt,  # honor our --halt arg
}
with ConnectHelper.session_with_chosen_probe(options=pyocd_options) as session:
    target = session.target # session.target (cortex_m), not args.target (stm32h5*3r*tx)
    target.halt()
    for elf in args.elf:
        image = load_image(elf)
        start = min(image)
        world = ("secure" if start in CONTROLLERS["secure"]["flash"]
                 else "non-secure")
        print(f"Flashing {elf} to {world} flash at 0x{start:08X}")
        t0 = time.time()
        flash(target, image)
        good, total = verify(target, image)
        if good != total:
            sys.exit(f"  VERIFY FAILED: {total - good} of {total} "
                     "16-byte quad-words don't match")
        print(f"  {total * 16} bytes written & verified in "
              f"{time.time() - t0:.1f} s")
        print(f"  {world.capitalize()} flash succeeded")
    # Reset with the NRST pin. ST: hot-plug software reset (-hardRst), which
    #                              HardFaulted even with correctly flashed images
    if args.halt:
        target.reset_and_halt(reset_type=Target.ResetType.HARDWARE)
        pc = target.read_core_register("pc")
        print(f"Halted at the secure reset entry (0x{pc:08X}), "
              "attach the debugger")
    elif not args.no_reset:
        target.reset(reset_type=Target.ResetType.HARDWARE)
        print("Reset, running the new image")
print(f"Done in {time.time() - started:.1f} s")
