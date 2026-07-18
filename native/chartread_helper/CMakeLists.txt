# chromiq-chartread — ChromIQ's chart-reading engine (issue #126).
#
# A fork of ArgyllCMS chartread.c (AGPL-3.0, Graeme W. Gill) linked against
# the vendored standalone instrument library under ../instlib (GPLv2+, the
# file set of Graeme's own instlib.ksh packaging). Library sources are
# compiled byte-identical to upstream — every ChromIQ change lives in
# chromiq_chartread.c / chromiq_json.c behind CHROMIQ_EXT, so a new Argyll
# release rebases by re-vendoring + re-applying a small diff.
cmake_minimum_required(VERSION 3.15)
project(chromiq_chartread C)

set(CMAKE_C_STANDARD 99)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(INSTLIB "${CMAKE_CURRENT_SOURCE_DIR}/../instlib")

# Mirror of spectro/Makefile.SA: OBJS + INSOBJS + SUPOBJS.
set(INSTLIB_UNITS
  conv sa_conv aglob inst numsup rspl1 icoms usbio hidio insttypes
  disptechs rspec xrga pollem xspect xdg_bds ccss ccmx pars cgats
  xcolorants xcal
  dtp20 dtp22 dtp41 dtp51 dtp92 ss ss_imp i1disp i1d3 i1pro i1pro_imp
  i1pro3 i1pro3_imp munki munki_imp hcfr huey colorhug spyd2 spydX
  specbos kleink10 smcube spydX2
  instappsup
)
set(INSTLIB_SOURCES "")
foreach(u ${INSTLIB_UNITS})
  list(APPEND INSTLIB_SOURCES "${INSTLIB}/${u}.c")
endforeach()

add_library(instlib STATIC ${INSTLIB_SOURCES})
target_include_directories(instlib PUBLIC ${INSTLIB})
# Makefile.SA CCDEFINES, verbatim.
target_compile_definitions(instlib PUBLIC
  SALONEINSTLIB ALLINSTS ENABLE_SERIAL ENABLE_FAST_SERIAL ENABLE_USB)
if(WIN32)
  target_compile_definitions(instlib PUBLIC NT)
elseif(APPLE)
  target_compile_definitions(instlib PUBLIC UNIX)
  # -Wno-implicit-function-declaration: Argyll 3.5.0's SALONEINSTLIB mode
  # misses a few prototypes (e.g. sa_Clamp3); upstream builds treat that as
  # a warning, modern clang as an error. Sources stay byte-identical.
  target_compile_options(instlib PRIVATE -fpascal-strings -Wno-sign-compare
    -Wno-implicit-function-declaration)
else()
  target_compile_definitions(instlib PUBLIC UNIX)
  target_compile_options(instlib PRIVATE -Wno-sign-compare
    -Wno-implicit-function-declaration)
endif()

add_executable(chromiq-chartread
  ${CMAKE_CURRENT_SOURCE_DIR}/chromiq_chartread.c
  ${CMAKE_CURRENT_SOURCE_DIR}/chromiq_json.c
  ${CMAKE_CURRENT_SOURCE_DIR}/chromiq_replay.c
  ${CMAKE_CURRENT_SOURCE_DIR}/chromiq_cal.c
  ${CMAKE_CURRENT_SOURCE_DIR}/chromiq_stubs.c
  ${INSTLIB}/alphix.c
)
target_include_directories(chromiq-chartread PRIVATE
  ${INSTLIB} ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(chromiq-chartread PRIVATE instlib)

if(APPLE)
  # Makefile.OSX LINKFLAGSDEF, verbatim.
  target_link_libraries(chromiq-chartread PRIVATE
    "-framework Carbon" "-framework Cocoa" "-framework IOKit"
    "-framework CoreFoundation" "-framework AudioToolbox" "-framework AppKit"
    m)
elseif(WIN32)
  target_link_libraries(chromiq-chartread PRIVATE
    winmm setupapi ws2_32 gdi32 user32 advapi32 shell32 ole32)
else()
  find_package(Threads REQUIRED)
  target_link_libraries(chromiq-chartread PRIVATE Threads::Threads m rt)
endif()
