cmake_minimum_required(VERSION 3.22)
# Paths point at the ST example's own Src/Inc/STM32CubeIDE layout.
set(MX_Defines_Syms
	USE_HAL_DRIVER
	STM32H533xx
    $<$<CONFIG:Debug>:DEBUG>
)
set(MX_Include_Dirs
    ${CMAKE_CURRENT_SOURCE_DIR}/Inc
    ${CMAKE_CURRENT_SOURCE_DIR}/../Secure_nsclib
    ${CMAKE_CURRENT_SOURCE_DIR}/../Drivers/STM32H5xx_HAL_Driver/Inc
    ${CMAKE_CURRENT_SOURCE_DIR}/../Drivers/CMSIS/Device/ST/STM32H5xx/Include
    ${CMAKE_CURRENT_SOURCE_DIR}/../Drivers/STM32H5xx_HAL_Driver/Inc/Legacy
    ${CMAKE_CURRENT_SOURCE_DIR}/../Drivers/CMSIS/Include
)
set(MX_Application_Src
    ${CMAKE_CURRENT_SOURCE_DIR}/Src/main.c
    ${CMAKE_CURRENT_SOURCE_DIR}/Src/stm32h5xx_it.c
    ${CMAKE_CURRENT_SOURCE_DIR}/Src/stm32h5xx_hal_msp.c
    ${CMAKE_CURRENT_SOURCE_DIR}/Src/secure_nsc.c
    ${CMAKE_CURRENT_SOURCE_DIR}/../STM32CubeIDE/Secure/Example/User/sysmem.c
    ${CMAKE_CURRENT_SOURCE_DIR}/../STM32CubeIDE/Secure/Example/User/syscalls.c
    ${CMAKE_CURRENT_SOURCE_DIR}/../STM32CubeIDE/Secure/Example/Startup/startup_stm32h533retx.s
)
# Compile the whole HAL. Modules the example's hal_conf.h leaves off compile
# to empty objects, and --gc-sections drops anything unreferenced.
file(GLOB STM32_HAL_SRC CONFIGURE_DEPENDS
    ${CMAKE_CURRENT_SOURCE_DIR}/../Drivers/STM32H5xx_HAL_Driver/Src/*.c)
list(FILTER STM32_HAL_SRC EXCLUDE REGEX "_template\\.c$")
set(STM32_Drivers_Src
    ${CMAKE_CURRENT_SOURCE_DIR}/Src/system_stm32h5xx_s.c
    ${STM32_HAL_SRC}
)

set(MX_LINK_DIRS )
set(MX_LINK_LIBS
    STM32_Drivers
    ${TOOLCHAIN_LINK_LIBRARIES}
)
add_library(stm32cubemx INTERFACE)
target_include_directories(stm32cubemx INTERFACE ${MX_Include_Dirs})
target_compile_definitions(stm32cubemx INTERFACE ${MX_Defines_Syms})

add_library(STM32_Drivers OBJECT)
target_sources(STM32_Drivers PRIVATE ${STM32_Drivers_Src})
target_link_libraries(STM32_Drivers PUBLIC stm32cubemx)

target_sources(${CMAKE_PROJECT_NAME} PRIVATE ${MX_Application_Src})
target_link_directories(${CMAKE_PROJECT_NAME} PRIVATE ${MX_LINK_DIRS})
target_link_libraries(${CMAKE_PROJECT_NAME} ${MX_LINK_LIBS})
set_target_properties(${CMAKE_PROJECT_NAME} PROPERTIES ADDITIONAL_CLEAN_FILES ${CMAKE_PROJECT_NAME}.map)
