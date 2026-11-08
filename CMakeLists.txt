cmake_minimum_required(VERSION 3.22)
include(ExternalProject)

# In order to use CMake for cross-compiling
set(CMAKE_CXX_COMPILER_FORCED "true")

set(CMAKE_PROJECT_NAME sec_flash_demo_tz)
project(${CMAKE_PROJECT_NAME})

# Spawns the Secure and NonSecure sub-builds
include("mx-generated.cmake")
