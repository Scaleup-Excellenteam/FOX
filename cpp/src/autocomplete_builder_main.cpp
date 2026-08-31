#include "autocomplete_builder.h"

#include <exception>
#include <filesystem>
#include <iostream>
#include <string>

int main(int argc, char* argv[]) {
  try {
    if (argc == 3 && std::string(argv[1]) == "--normalize") {
      const std::string input = argv[2];
      if (!autocomplete::builder::is_valid_utf8(input)) {
        throw std::runtime_error("normalization input is not valid UTF-8");
      }
      std::cout << autocomplete::builder::normalize(input);
      return 0;
    }
    if (argc != 5 || std::string(argv[1]) != "--corpus" ||
        std::string(argv[3]) != "--output") {
      std::cerr << "usage: autocomplete_builder --corpus CORPUS_ROOT "
                   "--output SNAPSHOT_DIRECTORY\n";
      return 2;
    }
    std::cerr << "[autocomplete_builder] start corpus=" << argv[2]
              << " output=" << argv[4] << '\n';
    return autocomplete::builder::build_snapshot(std::filesystem::path(argv[2]),
                                                 std::filesystem::path(argv[4]));
  } catch (const std::exception& error) {
    std::cerr << "autocomplete_builder: error: " << error.what() << '\n';
    return 1;
  }
}
