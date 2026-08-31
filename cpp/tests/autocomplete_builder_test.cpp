#include "autocomplete_builder.h"

#include <iostream>
#include <string>
#include <vector>

namespace {
bool expect(bool condition, const std::string& message) {
  if (!condition) std::cerr << "failure: " << message << '\n';
  return condition;
}
}  // namespace

int main() {
  bool passed = true;
  passed &= expect(autocomplete::builder::normalize("Hello,       WORLD!!!") ==
                       "hello world",
                   "ASCII normalization");
  passed &= expect(autocomplete::builder::normalize("alpha,beta.gamma") ==
                       "alphabetagamma",
                   "punctuation deletion");
  passed &= expect(autocomplete::builder::normalize("  !!!  ").empty(),
                   "normalized-empty input");
  passed &= expect(autocomplete::builder::normalize("Caf\xc3\xa9 \xe4\xb8\x96\xe7\x95\x8c") ==
                       "caf\xc3\xa9 \xe4\xb8\x96\xe7\x95\x8c",
                   "non-ASCII preservation");
  passed &= expect(autocomplete::builder::is_valid_utf8("\xd7\xa9\xd7\x9c\xd7\x95\xd7\x9d"),
                   "valid UTF-8");
  passed &= expect(!autocomplete::builder::is_valid_utf8("bad\xff"),
                   "invalid UTF-8");
  passed &= expect(autocomplete::builder::character_grams("banana", 1) ==
                       std::vector<std::string>({"a", "b", "n"}),
                   "deduplicated 1-grams");
  passed &= expect(autocomplete::builder::character_grams("banana", 2) ==
                       std::vector<std::string>({"an", "ba", "na"}),
                   "deduplicated 2-grams");
  passed &= expect(autocomplete::builder::character_grams("banana", 3) ==
                       std::vector<std::string>({"ana", "ban", "nan"}),
                   "deduplicated 3-grams");
  passed &= expect(autocomplete::builder::character_grams("hi", 3).empty(),
                   "short string");
  passed &= expect(autocomplete::builder::character_grams("\xd7\x90\xd7\x91", 1) ==
                       std::vector<std::string>({"\xd7\x90", "\xd7\x91"}),
                   "Unicode code-point grams");
  return passed ? 0 : 1;
}
