#pragma once

#include <cstdint>
#include <filesystem>
#include <map>
#include <string>
#include <utility>
#include <vector>

namespace autocomplete::builder {

using GramKey = std::pair<std::uint32_t, std::string>;
using PostingMap = std::map<GramKey, std::vector<std::uint32_t>>;

std::string normalize(const std::string& input);
bool is_valid_utf8(const std::string& input);
std::vector<std::string> character_grams(const std::string& normalized,
                                         std::uint32_t size);
int build_snapshot(const std::filesystem::path& corpus_root,
                   const std::filesystem::path& output_directory);

}  // namespace autocomplete::builder
