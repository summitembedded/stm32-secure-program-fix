set(ST_MULTICONTEXT TRUST_ZONE CACHE STRING "Type of multi-context")
#-----------------------Build Secure Project-----------------------#
if((${BUILD_CONTEXT} MATCHES .*Secure.*) OR (NOT DEFINED BUILD_CONTEXT))
    message("   Build context: " Secure)
    ExternalProject_Add(sec_flash_demo_s
        BINARY_DIR                  ${CMAKE_SOURCE_DIR}/Secure/build
        SOURCE_DIR                  ${PROJECT_SOURCE_DIR}/Secure
        PREFIX                      Secure
        CONFIGURE_HANDLED_BY_BUILD  true
        INSTALL_COMMAND             ""
        CMAKE_ARGS                  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON  -DCMAKE_TOOLCHAIN_FILE=${CMAKE_TOOLCHAIN_FILE}  -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
        BUILD_ALWAYS                true
    )
    set_property(DIRECTORY APPEND PROPERTY ADDITIONAL_CLEAN_FILES "${CMAKE_SOURCE_DIR}/Secure/build")
endif()
#-----------------------Build NonSecure Project-----------------------#
if((${BUILD_CONTEXT} MATCHES .*NonSecure.*) OR (NOT DEFINED BUILD_CONTEXT))
    message("   Build context: " NonSecure)
    ExternalProject_Add(sec_flash_demo_ns
        BINARY_DIR                  ${CMAKE_SOURCE_DIR}/NonSecure/build
        SOURCE_DIR                  ${PROJECT_SOURCE_DIR}/NonSecure
        PREFIX                      NonSecure
        CONFIGURE_HANDLED_BY_BUILD  true
        INSTALL_COMMAND             ""
        CMAKE_ARGS                  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON  -DCMAKE_TOOLCHAIN_FILE=${CMAKE_TOOLCHAIN_FILE}  -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
        BUILD_ALWAYS                true
        DEPENDS                     sec_flash_demo_s
    )
    set_property(DIRECTORY APPEND PROPERTY ADDITIONAL_CLEAN_FILES "${CMAKE_SOURCE_DIR}/NonSecure/build")
endif()
