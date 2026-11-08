cmake_minimum_required(VERSION 3.22)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Debug")
endif()
message("Build type: " ${CMAKE_BUILD_TYPE})

set(CMAKE_EXPORT_COMPILE_COMMANDS TRUE)

# -mcmse: this is the secure side, it emits the non-secure-callable veneers
set(STM32_MCU_FLAGS  "-mcpu=cortex-m33 -mfpu=fpv5-sp-d16 -mfloat-abi=hard -mcmse " )

# ST's own GCC linker script for the STiRoT secure region (from the example)
set(STM32_LINKER_SCRIPT ../STM32CubeIDE/Secure/STM32H533RETx_FLASH.ld)
# Emit the gateway import lib the NonSecure build links against
set(STM32_LINKER_OPTION  -Wl,--cmse-implib\ -Wl,--out-implib="${CMAKE_CURRENT_SOURCE_DIR}/../Secure_nsclib/secure_nsclib.o")

set(CMAKE_PROJECT_NAME sec_flash_demo_s)
project(${CMAKE_PROJECT_NAME})

add_executable(${CMAKE_PROJECT_NAME})

include("mx-generated.cmake")

set(CMAKE_CXX_COMPILER_FORCED "true")

enable_language(C ASM)

list(REMOVE_ITEM CMAKE_C_IMPLICIT_LINK_LIBRARIES ob)

if((CMAKE_C_STANDARD EQUAL 90) OR (CMAKE_C_STANDARD EQUAL 99))
    message(ERROR "Generated code requires C11 or higher")
endif()
