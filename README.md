# Demonstrate fix to STM32CubeProgrammer flash-fail-when-closed Issue

This code workspace demonstrates a fix to the issue described at https://www.summitembedded.com/blog/fixing-bugs-in-stm32s-secure-flashing-tool. ST's `STM32_Programmer_CLI` returns a `Fail to read NVM Size` error message when trying to JTAG/SWD flash a Trustzone device in the CLOSED state (even if you have the right Debug Authentication keys).

The core of our workaround is the [ROT_Provisioning/scripts/flash_closed.py](ROT_Provisioning/scripts/flash_closed.py) script.

This is based on a stock STM32CubeH5 V1.7.0 example (`NUCLEO-H533RE`,
`STiROT_Appli_TrustZone`) with a CMake build configuration. It runs on a [NUCLEO-H533RE Evaluation Board](https://www.st.com/en/evaluation-tools/nucleo-h533re.html), which you can purchase for less than $30.
- NOTE: this tutorial enables Debug Authentication on your board. If you do not wish to keep Debug Authentication enabled after completing this tutorial, remove it with the `./da.sh regress` command (see [Debug Authentication](#debug-authentication) below).

To see the original issue, checkout the git tag `original-issue` from this repo. To then test the fix, check out the `secure-flash-fix` tag.

## Requirements

- [STM32CubeCLT](https://www.st.com/en/development-tools/stm32cubeclt.html), with its tools on `PATH`
- pyocd & pyelftools (`pipx install pyocd` on Ubuntu)
- The pyocd CMSIS pack for the flash layout:
  `pyocd pack install stm32h533retx`
- VS Code with the `cortex-debug` extension, for F5 debugging
- `picocom` (or other serial viewer) to see the device's output messages: `sudo apt install picocom`

## Semi-Automated VSCode Build/Flash

To build the code and flash the device, you can simply run the `TZ (JTAG): Build & Debug` configuration in VSCode's debug menu.

If you prefer to build manually, run:

```
cmake --preset Debug
cmake --build --preset Debug
```

That builds `Secure/build/sec_flash_demo_s.elf`,
`NonSecure/build/sec_flash_demo_ns.elf` and the CMSE gateway import lib
`Secure_nsclib/secure_nsclib.o`.

## Provision the board (once)

The first time you run the VSCode `TZ (JTAG): Build & Debug` configuration, it will give the error message: 

`ERROR: product is in the Open state. This demo must be run in a non-open state (provisioned or closed).`

To put your Nucleo STM32H533 board into the provision or closed state, simply use the provided script.

WARNING: This will enable Debug Authentication using ST's example DA keys (cloned into this repo). It's pretty simple to work with Debug Authentication. But if you're not aware that it's enabled, it may appear as though your board is non-responsive. See the [Debug Authentican](#debug-authentication) section below for working with (or regressing/removing) debug authentication.

```
cd ROT_Provisioning/scripts
./provision.sh provisioned     # or: ./provision.sh closed
```

WARNING: ST's tools will print `Error: failed to reconnect after reset !` at the end. Don't be alarmed. The process to enable debug-auth uses unauthenticated debugging. So the final step (reboot into debug-auth-enabled mode) causes the unauthenticated session to lose connection. Start a new authenticated session with `./da.sh discover` to see that your board is still responsive:

``` bash
$ ./provision.sh provisioned
...

Reconnecting...
Error: failed to reconnect after reset !
Error: Unable to reconnect after setting the Option Bytes

provision.sh is done.
Dont mind the "Error: ... failed ... " messages above.
Check with: ./da.sh discover

$ ./da.sh discover
...
Start Debug Authentication Sequence
...
Debug Authentication: Discovery Success
```

## Debug Authentication

This app runs at HDPL1, so it needs `eh` permissions (secure & non-secure
debug at HDPL1). Thus each subsquent flash will unlock debug authentication with:

``` bash
cd ROT_Provisioning/scripts;
./da.sh unlock eh
```

Other `da.sh` commands include:
```
cd ROT_Provisioning/scripts;
./da.sh discover      # product state + permission letters
./da.sh ensure eh     # unlock only if debug is closed
./da.sh close         # close debug again
./da.sh regress       # back to Open, wipes the board
```

The keypairs & certificates under `ROT_Provisioning/DA/Keys` and
`ROT_Provisioning/DA/Certificates` are ST's dev samples for this demo. They were copied from the Cube sample code installed locally in `~/STM32Cube/Repository/STM32Cube_FW_H5_V1.7.0/Projects/NUCLEO-H533RE/ROT_Provisioning/`. They are for demonstration purposes only and should no be used in production.

## Flash

Once the device is provisioned or closed, again you can simply run the `TZ (JTAG): Build & Debug` configuration in VSCode's debug menu to re-build and flash the device.

If you prefer to flash manually, mimic the command in the the `TZ: flash` task in `.vscode/tasks.json`. This command changes depending on wether you've checked out the `original-issue` tag or the `secure-flash-fix` tag.

## Check the flash worked

- If you've checked out the `original-issue` tag:
    - The flash process will fail with the error `Fail to read NVM Size`. This is expected - you've recreated the original issue explained in 
     https://www.summitembedded.com/blog/fixing-bugs-in-stm32s-secure-flashing-tool!

- If you've checked out the `secure-flash-fix` tag:
    - The VSCode task will build, unlock debug authentication, check your option bytes, and flash the firmware
    - When the new firmware boots, the secure world prints a build stamp over the ST-LINK virtual COM port (USART2) before handing off to the non-secure side. View this with:

        ```
        picocom -b 115200 /dev/ttyACM0
        
        # prints:
        # [SECURE] Hello world. Secure image built <date> <time>
        ```
        - The <date> <time> stamp comes from `__DATE__`/`__TIME__` in gcc. A fresh value is written to flash each time you run the VSCode task, to prove that the secure flash write worked and the bug is fixed. If it prints a stale timestamp, the flash write did not succeed.
