cmake_minimum_required(VERSION 3.28)

project(autocomplete_phase0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Protobuf 3.21.12 EXACT REQUIRED)

if(NOT Protobuf_PROTOC_EXECUTABLE)
  message(FATAL_ERROR "The system protoc executable was not found")
endif()

message(STATUS "Using protoc: ${Protobuf_PROTOC_EXECUTABLE}")
message(STATUS "Using Protobuf: ${Protobuf_VERSION}")

set(REPOSITORY_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/..")
set(PROTO_DIRECTORY "${REPOSITORY_ROOT}/proto")
set(PROTO_SCHEMA "${PROTO_DIRECTORY}/autocomplete_snapshot.proto")
set(GENERATED_PROTO_DIRECTORY "${REPOSITORY_ROOT}/build/generated/proto")
set(GENERATED_PROTO_SOURCE
    "${GENERATED_PROTO_DIRECTORY}/autocomplete_snapshot.pb.cc")
set(GENERATED_PROTO_HEADER
    "${GENERATED_PROTO_DIRECTORY}/autocomplete_snapshot.pb.h")

add_custom_command(
  OUTPUT "${GENERATED_PROTO_SOURCE}" "${GENERATED_PROTO_HEADER}"
  COMMAND "${CMAKE_COMMAND}" -E make_directory "${GENERATED_PROTO_DIRECTORY}"
  COMMAND
    "${Protobuf_PROTOC_EXECUTABLE}"
    "--proto_path=${PROTO_DIRECTORY}"
    "--cpp_out=${GENERATED_PROTO_DIRECTORY}"
    "${PROTO_SCHEMA}"
  DEPENDS "${PROTO_SCHEMA}"
  COMMENT "Generating C++ bindings from the frozen autocomplete schema"
  VERBATIM
)

add_library(autocomplete_snapshot_proto STATIC
            "${GENERATED_PROTO_SOURCE}" "${GENERATED_PROTO_HEADER}")
target_include_directories(autocomplete_snapshot_proto
                           PUBLIC "${GENERATED_PROTO_DIRECTORY}")
target_link_libraries(autocomplete_snapshot_proto PUBLIC protobuf::libprotobuf)

enable_testing()

add_executable(phase0_protobuf_smoke tests/protobuf_smoke_test.cpp)
target_link_libraries(phase0_protobuf_smoke PRIVATE autocomplete_snapshot_proto)
add_test(NAME phase0_protobuf_smoke COMMAND phase0_protobuf_smoke)
