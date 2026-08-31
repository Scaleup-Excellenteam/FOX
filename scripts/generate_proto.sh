#!/usr/bin/env bash
set -euo pipefail

readonly EXPECTED_PROTOC_VERSION="libprotoc 3.21.12"

if ! command -v protoc >/dev/null 2>&1; then
  echo "error: protoc is required but was not found on PATH" >&2
  exit 1
fi

actual_protoc_version="$(protoc --version)"
echo "protoc version: ${actual_protoc_version}"

if [[ "${actual_protoc_version}" != "${EXPECTED_PROTOC_VERSION}" ]]; then
  echo "error: expected ${EXPECTED_PROTOC_VERSION}, got ${actual_protoc_version}" >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "${script_dir}/.." && pwd)"
proto_dir="${repository_root}/proto"
proto_file="${proto_dir}/autocomplete_snapshot.proto"
python_output_dir="${repository_root}/src/autocomplete/generated"
cpp_output_dir="${repository_root}/build/generated/proto"

mkdir -p -- "${python_output_dir}" "${cpp_output_dir}"

protoc \
  --proto_path="${proto_dir}" \
  --python_out="${python_output_dir}" \
  --cpp_out="${cpp_output_dir}" \
  "${proto_file}"

echo "generated Python binding in ${python_output_dir}"
echo "generated C++ bindings in ${cpp_output_dir}"
