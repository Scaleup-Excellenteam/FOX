cmake_minimum_required(VERSION 3.20)
project(fox_snapshot LANGUAGES CXX)
find_package(OpenSSL REQUIRED)
find_package(Protobuf 3.21 REQUIRED)
add_executable(fox_snapshot_builder src/snapshot_builder.cpp generated/autocomplete_snapshot.pb.cc)
target_compile_features(fox_snapshot_builder PRIVATE cxx_std_17)
target_compile_options(fox_snapshot_builder PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_include_directories(fox_snapshot_builder PRIVATE generated ${Protobuf_INCLUDE_DIRS})
target_link_libraries(fox_snapshot_builder PRIVATE OpenSSL::Crypto ${Protobuf_LIBRARIES})
enable_testing()
add_test(NAME builder_usage COMMAND fox_snapshot_builder)
set_tests_properties(builder_usage PROPERTIES WILL_FAIL TRUE)
add_test(NAME normalization_smoke COMMAND fox_snapshot_builder --normalize "Hello,       WORLD!!!")
set_tests_properties(normalization_smoke PROPERTIES PASS_REGULAR_EXPRESSION "hello world")
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/generated)

include_directories(${CMAKE_CURRENT_SOURCE_DIR}/generated)

include_directories(${CMAKE_CURRENT_SOURCE_DIR}/generated)
add_executable(fox_search_cli 
    src/search_cli.cpp 
    generated/autocomplete_snapshot.pb.cc
)
target_link_libraries(fox_search_cli PRIVATE ${PROTOBUF_LIBRARIES})
