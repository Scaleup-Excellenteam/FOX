#include "autocomplete_builder.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include <google/protobuf/io/coded_stream.h>
#include <google/protobuf/io/zero_copy_stream_impl_lite.h>
#include <openssl/evp.h>

#include "autocomplete_snapshot.pb.h"

namespace fs = std::filesystem;

namespace autocomplete::builder {
namespace {

constexpr std::size_t kMaximumPayloadBytes = 8U * 1024U * 1024U;
constexpr std::string_view kAsciiPunctuation =
    "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~";
constexpr std::string_view kRecordsFile = "records.binpb";
constexpr std::string_view kIndexFile = "index.binpb";
constexpr std::string_view kManifestFile = "manifest.binpb";

class Sha256 {
 public:
  Sha256() : context_(EVP_MD_CTX_new()) {
    if (context_ == nullptr ||
        EVP_DigestInit_ex(context_, EVP_sha256(), nullptr) != 1) {
      throw std::runtime_error("cannot initialize SHA-256");
    }
  }

  ~Sha256() { EVP_MD_CTX_free(context_); }
  Sha256(const Sha256&) = delete;
  Sha256& operator=(const Sha256&) = delete;

  void update(std::string_view value) {
    if (!value.empty() &&
        EVP_DigestUpdate(context_, value.data(), value.size()) != 1) {
      throw std::runtime_error("cannot update SHA-256");
    }
  }

  void update(const std::array<unsigned char, 8>& value) {
    if (EVP_DigestUpdate(context_, value.data(), value.size()) != 1) {
      throw std::runtime_error("cannot update SHA-256");
    }
  }

  void update(const std::array<unsigned char, 4>& value) {
    if (EVP_DigestUpdate(context_, value.data(), value.size()) != 1) {
      throw std::runtime_error("cannot update SHA-256");
    }
  }

  std::string finish_hex() {
    std::array<unsigned char, EVP_MAX_MD_SIZE> bytes{};
    unsigned int size = 0;
    if (EVP_DigestFinal_ex(context_, bytes.data(), &size) != 1) {
      throw std::runtime_error("cannot finalize SHA-256");
    }
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (unsigned int index = 0; index < size; ++index) {
      output << std::setw(2) << static_cast<unsigned int>(bytes[index]);
    }
    return output.str();
  }

 private:
  EVP_MD_CTX* context_;
};

std::array<unsigned char, 8> u64_be(std::uint64_t value) {
  std::array<unsigned char, 8> bytes{};
  for (std::size_t index = 0; index < bytes.size(); ++index) {
    bytes[bytes.size() - index - 1] =
        static_cast<unsigned char>(value & 0xffU);
    value >>= 8U;
  }
  return bytes;
}

std::array<unsigned char, 4> u32_be(std::uint32_t value) {
  std::array<unsigned char, 4> bytes{};
  for (std::size_t index = 0; index < bytes.size(); ++index) {
    bytes[bytes.size() - index - 1] =
        static_cast<unsigned char>(value & 0xffU);
    value >>= 8U;
  }
  return bytes;
}

void update_string(Sha256& digest, std::string_view value) {
  digest.update(u64_be(value.size()));
  digest.update(value);
}

std::string sha256_hex(std::string_view value) {
  Sha256 digest;
  digest.update(value);
  return digest.finish_hex();
}

std::string serialize_deterministically(const google::protobuf::Message& message) {
  const std::size_t size = message.ByteSizeLong();
  if (size == 0 || size > kMaximumPayloadBytes ||
      size > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
    throw std::runtime_error("protobuf payload size is invalid or exceeds 8 MiB");
  }
  std::string payload(size, '\0');
  google::protobuf::io::ArrayOutputStream raw_output(payload.data(),
                                                      payload.size());
  google::protobuf::io::CodedOutputStream coded_output(&raw_output);
  coded_output.SetSerializationDeterministic(true);
  if (!message.SerializeToCodedStream(&coded_output) || coded_output.HadError()) {
    throw std::runtime_error("cannot serialize protobuf message");
  }
  return payload;
}

class FramedWriter {
 public:
  explicit FramedWriter(const fs::path& path)
      : path_(path), output_(path, std::ios::binary | std::ios::trunc) {
    if (!output_) {
      throw std::runtime_error("cannot create " + path.string());
    }
  }

  void write(const google::protobuf::Message& message) {
    const std::string payload = serialize_deterministically(message);
    const auto prefix = u32_be(static_cast<std::uint32_t>(payload.size()));
    output_.write(reinterpret_cast<const char*>(prefix.data()), prefix.size());
    output_.write(payload.data(), static_cast<std::streamsize>(payload.size()));
    if (!output_) {
      throw std::runtime_error("cannot write " + path_.string());
    }
  }

  void close() {
    output_.flush();
    output_.close();
    if (!output_) {
      throw std::runtime_error("cannot finalize " + path_.string());
    }
  }

 private:
  fs::path path_;
  std::ofstream output_;
};

void write_file(const fs::path& path, std::string_view bytes) {
  std::ofstream output(path, std::ios::binary | std::ios::trunc);
  if (!output ||
      !output.write(bytes.data(), static_cast<std::streamsize>(bytes.size()))) {
    throw std::runtime_error("cannot write " + path.string());
  }
  output.flush();
  output.close();
  if (!output) {
    throw std::runtime_error("cannot finalize " + path.string());
  }
}

std::string lowercase_extension(fs::path path) {
  std::string extension = path.extension().string();
  std::transform(extension.begin(), extension.end(), extension.begin(),
                 [](unsigned char value) {
                   return static_cast<char>(std::tolower(value));
                 });
  return extension;
}

std::vector<std::pair<std::string, fs::path>> discover_files(
    const fs::path& root) {
  std::vector<std::pair<std::string, fs::path>> files;
  for (const fs::directory_entry& entry : fs::recursive_directory_iterator(root)) {
    if (!entry.is_regular_file() || lowercase_extension(entry.path()) != ".txt") {
      continue;
    }
    const std::string relative = fs::relative(entry.path(), root).generic_string();
    if (relative.empty() || relative.front() == '/' ||
        relative == ".." || relative.rfind("../", 0) == 0) {
      throw std::runtime_error("unsafe corpus path: " + relative);
    }
    files.emplace_back(relative, entry.path());
  }
  std::sort(files.begin(), files.end(),
            [](const auto& left, const auto& right) {
              return left.first < right.first;
            });
  return files;
}

std::vector<std::size_t> codepoint_offsets(const std::string& input) {
  std::vector<std::size_t> offsets;
  offsets.reserve(input.size() + 1);
  for (std::size_t offset = 0; offset < input.size();) {
    offsets.push_back(offset);
    const unsigned char lead = static_cast<unsigned char>(input[offset]);
    offset += lead < 0x80U ? 1U : (lead < 0xe0U ? 2U : (lead < 0xf0U ? 3U : 4U));
  }
  offsets.push_back(input.size());
  return offsets;
}

fs::path staging_path_for(const fs::path& output) {
  const auto ticks = std::chrono::steady_clock::now().time_since_epoch().count();
  return output.parent_path() /
         ("." + output.filename().string() + ".incomplete-" +
          std::to_string(ticks));
}

class StagingGuard {
 public:
  explicit StagingGuard(fs::path path) : path_(std::move(path)) {}
  ~StagingGuard() {
    if (!published_) {
      std::error_code ignored;
      fs::remove_all(path_, ignored);
    }
  }
  void published() { published_ = true; }

 private:
  fs::path path_;
  bool published_ = false;
};

}  // namespace

bool is_valid_utf8(const std::string& input) {
  for (std::size_t offset = 0; offset < input.size();) {
    const unsigned char lead = static_cast<unsigned char>(input[offset]);
    std::size_t width = 0;
    if (lead < 0x80U) {
      width = 1;
    } else if (lead >= 0xc2U && lead <= 0xdfU) {
      width = 2;
    } else if (lead >= 0xe0U && lead <= 0xefU) {
      width = 3;
    } else if (lead >= 0xf0U && lead <= 0xf4U) {
      width = 4;
    } else {
      return false;
    }
    if (offset + width > input.size()) {
      return false;
    }
    for (std::size_t index = 1; index < width; ++index) {
      if ((static_cast<unsigned char>(input[offset + index]) & 0xc0U) != 0x80U) {
        return false;
      }
    }
    if ((width == 3 && lead == 0xe0U &&
         static_cast<unsigned char>(input[offset + 1]) < 0xa0U) ||
        (width == 3 && lead == 0xedU &&
         static_cast<unsigned char>(input[offset + 1]) >= 0xa0U) ||
        (width == 4 && lead == 0xf0U &&
         static_cast<unsigned char>(input[offset + 1]) < 0x90U) ||
        (width == 4 && lead == 0xf4U &&
         static_cast<unsigned char>(input[offset + 1]) >= 0x90U)) {
      return false;
    }
    offset += width;
  }
  return true;
}

std::string normalize(const std::string& input) {
  std::string output;
  output.reserve(input.size());
  bool pending_space = false;
  for (const unsigned char value : input) {
    if (value == ' ') {
      pending_space = !output.empty();
      continue;
    }
    if (value < 0x80U &&
        kAsciiPunctuation.find(static_cast<char>(value)) != std::string_view::npos) {
      continue;
    }
    if (pending_space) {
      output.push_back(' ');
      pending_space = false;
    }
    output.push_back(value >= 'A' && value <= 'Z'
                         ? static_cast<char>(value + ('a' - 'A'))
                         : static_cast<char>(value));
  }
  return output;
}

std::vector<std::string> character_grams(const std::string& normalized,
                                         std::uint32_t size) {
  if (size < 1 || size > 3 || !is_valid_utf8(normalized)) {
    throw std::invalid_argument("gram input or size is invalid");
  }
  const std::vector<std::size_t> offsets = codepoint_offsets(normalized);
  const std::size_t count = offsets.size() - 1;
  std::vector<std::string> grams;
  if (count < size) {
    return grams;
  }
  grams.reserve(count - size + 1);
  for (std::size_t index = 0; index + size <= count; ++index) {
    grams.emplace_back(normalized.substr(offsets[index],
                                         offsets[index + size] - offsets[index]));
  }
  std::sort(grams.begin(), grams.end());
  grams.erase(std::unique(grams.begin(), grams.end()), grams.end());
  return grams;
}

int build_snapshot(const fs::path& corpus_root,
                   const fs::path& output_directory) {
  const auto started = std::chrono::steady_clock::now();
  const fs::path root = fs::absolute(corpus_root).lexically_normal();
  const fs::path output = fs::absolute(output_directory).lexically_normal();
  if (!fs::is_directory(root)) {
    throw std::runtime_error("corpus root is not a directory: " + root.string());
  }
  if (fs::exists(output)) {
    throw std::runtime_error("snapshot destination already exists: " +
                             output.string());
  }
  if (!output.has_parent_path() || !fs::is_directory(output.parent_path())) {
    throw std::runtime_error("snapshot parent directory does not exist: " +
                             output.parent_path().string());
  }

  const auto files = discover_files(root);
  const fs::path staging = staging_path_for(output);
  if (!fs::create_directory(staging)) {
    throw std::runtime_error("cannot create staging directory: " + staging.string());
  }
  StagingGuard staging_guard(staging);
  FramedWriter records_writer(staging / kRecordsFile);
  PostingMap postings;
  Sha256 corpus_digest;
  std::uint64_t line_count = 0;
  std::uint64_t skipped_count = 0;
  std::uint64_t sentence_id = 0;

  for (const auto& [relative, path] : files) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
      throw std::runtime_error("cannot read corpus file: " + relative);
    }
    std::string line;
    std::uint64_t line_number = 0;
    bool first_line = true;
    while (std::getline(input, line)) {
      ++line_number;
      ++line_count;
      if (!line.empty() && line.back() == '\r') {
        line.pop_back();
      }
      if (first_line && line.size() >= 3 &&
          static_cast<unsigned char>(line[0]) == 0xefU &&
          static_cast<unsigned char>(line[1]) == 0xbbU &&
          static_cast<unsigned char>(line[2]) == 0xbfU) {
        line.erase(0, 3);
      }
      first_line = false;
      if (!is_valid_utf8(line)) {
        throw std::runtime_error("invalid UTF-8 in " + relative + ":" +
                                 std::to_string(line_number));
      }
      const std::string normalized = normalize(line);
      if (normalized.empty()) {
        ++skipped_count;
        continue;
      }
      if (sentence_id == std::numeric_limits<std::uint32_t>::max()) {
        throw std::runtime_error("sentence count exceeds uint32 posting capacity");
      }
      ++sentence_id;
      ::autocomplete::snapshot::v1::SentenceRecordProto record;
      record.set_sentence_id(sentence_id);
      record.set_original(line);
      record.set_normalized(normalized);
      record.set_source_path(relative);
      record.set_line_number(line_number);
      records_writer.write(record);

      corpus_digest.update(u64_be(sentence_id));
      update_string(corpus_digest, relative);
      corpus_digest.update(u64_be(line_number));
      update_string(corpus_digest, line);
      update_string(corpus_digest, normalized);

      for (std::uint32_t size = 1; size <= 3; ++size) {
        for (const std::string& gram : character_grams(normalized, size)) {
          postings[{size, gram}].push_back(static_cast<std::uint32_t>(sentence_id));
        }
      }
    }
    if (input.bad()) {
      throw std::runtime_error("read failure in corpus file: " + relative);
    }
  }
  records_writer.close();

  FramedWriter index_writer(staging / kIndexFile);
  Sha256 index_digest;
  std::uint64_t posting_entries = 0;
  for (const auto& [key, ids] : postings) {
    ::autocomplete::snapshot::v1::GramPostingProto posting;
    posting.set_gram_size(key.first);
    posting.set_gram(key.second);
    for (const std::uint32_t id : ids) {
      posting.add_sentence_ids(id);
    }
    index_writer.write(posting);
    index_digest.update(u32_be(key.first));
    update_string(index_digest, key.second);
    index_digest.update(u64_be(ids.size()));
    for (const std::uint32_t id : ids) {
      index_digest.update(u64_be(id));
    }
    posting_entries += ids.size();
  }
  index_writer.close();

  const std::string corpus_hex = corpus_digest.finish_hex();
  const std::string index_hex = index_digest.finish_hex();
  const std::string identity =
      "corpus_digest_sha256=" + corpus_hex + "\n" +
      "index_digest_sha256=" + index_hex + "\n" +
      "schema_version=1\nnormalization_version=1\n" +
      "index_strategy_version=1\ngram_sizes=1,2,3\n";
  const std::string snapshot_id = sha256_hex(identity);

  ::autocomplete::snapshot::v1::SnapshotManifestProto manifest;
  manifest.set_schema_version(1);
  manifest.set_normalization_version(1);
  manifest.set_index_strategy_version(1);
  for (const std::uint32_t size : {1U, 2U, 3U}) {
    manifest.add_gram_sizes(size);
  }
  manifest.set_corpus_digest_sha256(corpus_hex);
  manifest.set_snapshot_id(snapshot_id);
  manifest.set_created_at_utc("1970-01-01T00:00:00Z");
  manifest.add_record_files(std::string(kRecordsFile));
  manifest.add_index_files(std::string(kIndexFile));
  manifest.set_searchable_record_count(sentence_id);
  manifest.set_posting_count(postings.size());
  manifest.set_index_digest_sha256(index_hex);
  write_file(staging / kManifestFile, serialize_deterministically(manifest));

  fs::rename(staging, output);
  staging_guard.published();
  const auto elapsed = std::chrono::duration<double>(
      std::chrono::steady_clock::now() - started);
  std::cerr << "[autocomplete_builder] complete"
            << " files=" << files.size() << " lines=" << line_count
            << " accepted=" << sentence_id << " skipped=" << skipped_count
            << " grams=" << postings.size()
            << " posting_ids=" << posting_entries
            << " elapsed_seconds=" << std::fixed << std::setprecision(3)
            << elapsed.count() << " output=" << output.string()
            << " snapshot_id=" << snapshot_id << '\n';
  std::cout << "snapshot_id=" << snapshot_id << " sentences=" << sentence_id
            << " files=" << files.size() << '\n';
  return 0;
}

}  // namespace autocomplete::builder
