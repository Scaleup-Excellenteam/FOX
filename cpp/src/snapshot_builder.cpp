#include "autocomplete_snapshot.pb.h"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <openssl/evp.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <sys/resource.h>
#include <vector>
namespace fs = std::filesystem;
struct Record {
  uint64_t id, line;
  std::string path, original, normalized;
};
using Key = std::pair<uint32_t, std::string>;
using Clock = std::chrono::high_resolution_clock;
using TimePoint = std::chrono::time_point<Clock>;

struct Meta {
  std::string name;
  uint32_t kind;
  uint64_t size, count;
  std::string hash;
  Key first, last;
};

double elapsed_ms(TimePoint started, TimePoint finished) {
  return std::chrono::duration<double, std::milli>(finished - started).count();
}
double peak_rss_mb() {
  struct rusage usage {};
  if (getrusage(RUSAGE_SELF, &usage) != 0)
    return 0.0;
  return static_cast<double>(usage.ru_maxrss) / 1024.0;
}

uint32_t crc32c(const std::string &data) {
  uint32_t crc = ~0u;
  for (unsigned char c : data) {
    crc ^= c;
    for (int i = 0; i < 8; i++)
      crc = (crc >> 1) ^ ((crc & 1) ? 0x82f63b78u : 0);
  }
  return ~crc;
}
class Sha256Stream {
public:
  Sha256Stream() : context_(EVP_MD_CTX_new()) {
    if (!context_ || EVP_DigestInit_ex(context_, EVP_sha256(), nullptr) != 1)
      throw std::runtime_error("cannot initialize SHA-256");
  }
  ~Sha256Stream() { EVP_MD_CTX_free(context_); }
  Sha256Stream(const Sha256Stream &) = delete;
  Sha256Stream &operator=(const Sha256Stream &) = delete;
  void update(const void *data, size_t size) {
    if (size != 0 && EVP_DigestUpdate(context_, data, size) != 1)
      throw std::runtime_error("cannot update SHA-256");
  }
  void update(std::string_view value) { update(value.data(), value.size()); }
  void update_le64(uint64_t value) {
    std::array<unsigned char, 8> encoded{};
    for (size_t index = 0; index < encoded.size(); ++index)
      encoded[index] = static_cast<unsigned char>(value >> (8 * index));
    update(encoded.data(), encoded.size());
  }
  void update_identity_string(std::string_view value) {
    update_le64(value.size());
    update(value);
  }
  std::string finish() {
    std::string output(EVP_MAX_MD_SIZE, '\0');
    unsigned int size = 0;
    if (EVP_DigestFinal_ex(context_,
                           reinterpret_cast<unsigned char *>(output.data()),
                           &size) != 1)
      throw std::runtime_error("cannot finalize SHA-256");
    output.resize(size);
    return output;
  }
private:
  EVP_MD_CTX *context_;
};
std::string sha(std::string_view data) {
  Sha256Stream digest;
  digest.update(data);
  return digest.finish();
}
std::string hex(const std::string &data) {
  static const char *h = "0123456789abcdef";
  std::string out;
  for (unsigned char c : data) {
    out += h[c >> 4];
    out += h[c & 15];
  }
  return out;
}
void le32(std::string &out, uint32_t x) {
  for (int i = 0; i < 4; i++)
    out.push_back(char(x >> (8 * i)));
}
bool valid_utf8(const std::string &s) {
  for (size_t i = 0; i < s.size();) {
    unsigned char c = s[i];
    size_t n = c < 128 ? 1
                       : (c >= 0xc2 && c <= 0xdf
                              ? 2
                              : (c >= 0xe0 && c <= 0xef
                                     ? 3
                                     : (c >= 0xf0 && c <= 0xf4 ? 4 : 0)));
    if (!n || i + n > s.size())
      return false;
    for (size_t j = 1; j < n; j++)
      if ((static_cast<unsigned char>(s[i + j]) & 0xc0) != 0x80)
        return false;
    if (n == 3 && c == 0xe0 && static_cast<unsigned char>(s[i + 1]) < 0xa0)
      return false;
    if (n == 3 && c == 0xed && static_cast<unsigned char>(s[i + 1]) >= 0xa0)
      return false;
    if (n == 4 && c == 0xf0 && static_cast<unsigned char>(s[i + 1]) < 0x90)
      return false;
    if (n == 4 && c == 0xf4 && static_cast<unsigned char>(s[i + 1]) >= 0x90)
      return false;
    i += n;
  }
  return true;
}
void add_grams(const std::string &normalized, uint64_t sentence_id,
               std::map<Key, std::vector<uint32_t>> &postings) {
  thread_local std::vector<size_t> offsets;
  thread_local std::vector<std::string_view> grams;
  offsets.clear();
  for (size_t offset = 0; offset < normalized.size();) {
    offsets.push_back(offset);
    const auto lead = static_cast<unsigned char>(normalized[offset]);
    offset += lead < 128 ? 1 : (lead < 224 ? 2 : (lead < 240 ? 3 : 4));
  }
  offsets.push_back(normalized.size());
  const size_t codepoint_count = offsets.size() - 1;
  for (uint32_t size = 1; size <= 3; ++size) {
    grams.clear();
    if (codepoint_count >= size) {
      grams.reserve(codepoint_count - size + 1);
      for (size_t index = 0; index + size <= codepoint_count; ++index)
        grams.emplace_back(normalized.data() + offsets[index],
                           offsets[index + size] - offsets[index]);
    }
    std::sort(grams.begin(), grams.end());
    grams.erase(std::unique(grams.begin(), grams.end()), grams.end());
    for (std::string_view gram : grams)
      postings[{size, std::string(gram)}].push_back(sentence_id);
  }
}
std::string normalize(const std::string &input) {
  std::string out;
  bool pending = false;
  const std::string punct = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~";
  for (unsigned char c : input) {
    if (c == ' ') {
      if (!out.empty())
        pending = true;
      continue;
    }
    if (c < 128 && punct.find(char(c)) != std::string::npos)
      continue;
    if (pending) {
      out.push_back(' ');
      pending = false;
    }
    out.push_back(c >= 'A' && c <= 'Z' ? char(c + 32) : char(c));
  }
  return out;
}

std::string proto_record(const Record &record) {
  fox::autocomplete::snapshot::v1::SentenceRecord message;
  message.set_sentence_id(record.id);
  message.set_source_relative_path(record.path);
  message.set_source_line_number(record.line);
  message.set_original_text(record.original);
  message.set_normalized_text(record.normalized);
  return message.SerializeAsString();
}

std::string proto_posting(const Key &key, const std::vector<uint32_t> &ids,
                          size_t begin, size_t end, uint32_t chunk_index,
                          bool is_last_chunk) {
  fox::autocomplete::snapshot::v1::PostingChunk message;
  message.set_gram_size(key.first);
  message.set_gram(key.second);
  message.set_chunk_index(chunk_index);
  message.set_is_last_chunk(is_last_chunk);
  for (size_t index = begin; index < end; ++index)
    message.add_sentence_ids(ids[index]);
  return message.SerializeAsString();
}

void populate_meta(const Meta &source,
                   fox::autocomplete::snapshot::v1::ShardMetadata *message) {
  message->set_file_name(source.name);
  message->set_kind(
      static_cast<fox::autocomplete::snapshot::v1::ShardKind>(source.kind));
  message->set_framed_size_bytes(source.size);
  message->set_frame_count(source.count);
  message->set_sha256(source.hash);
  if (source.kind == 2) {
    message->set_first_gram_size(source.first.first);
    message->set_first_gram(source.first.second);
    message->set_last_gram_size(source.last.first);
    message->set_last_gram(source.last.second);
  }
}

std::string proto_manifest(uint64_t count,
                           const std::vector<Meta> &record_shards,
                           const std::vector<Meta> &index_shards,
                           const std::string &corpus_digest,
                           const std::string &snapshot_digest,
                           const std::string &index_digest,
                           uint64_t shard_target_bytes) {
  fox::autocomplete::snapshot::v1::SnapshotManifest message;
  message.set_schema_version(1);
  message.set_framing_version(1);
  message.set_sentence_count(count);
  message.mutable_normalization()->set_version(1);
  message.mutable_normalization()->set_algorithm("ascii-v1");
  auto *index = message.mutable_ngram_index();
  index->set_version(1);
  for (uint32_t size : {1, 2, 3})
    index->add_gram_codepoints(size);
  index->set_min_selective_query_codepoints(2);
  index->set_shard_target_bytes(shard_target_bytes);
  for (const auto &shard : record_shards)
    populate_meta(shard, message.add_record_shards());
  for (const auto &shard : index_shards)
    populate_meta(shard, message.add_index_shards());
  message.set_snapshot_id(hex(snapshot_digest));
  message.set_corpus_digest(corpus_digest);
  message.set_created_at_utc("1970-01-01T00:00:00Z");
  message.set_index_digest(index_digest);
  return message.SerializeAsString();
}
std::string frame(uint32_t kind, const std::vector<std::string> &payloads) {
  std::string out = "FOXSNAP1";
  le32(out, 1);
  le32(out, kind);
  for (auto &p : payloads) {
    if (p.size() > 8 * 1024 * 1024)
      throw std::runtime_error("protobuf payload exceeds 8 MiB");
    le32(out, p.size());
    out += p;
    le32(out, crc32c(p));
  }
  return out;
}
void write(const fs::path &p, const std::string &data) {
  std::ofstream f(p, std::ios::binary);
  if (!f || !f.write(data.data(), data.size()))
    throw std::runtime_error("cannot write " + p.string());
}
class ShardWriter {
public:
  ShardWriter(fs::path directory, std::string prefix, uint32_t kind,
              size_t target)
      : directory_(std::move(directory)), prefix_(std::move(prefix)),
        kind_(kind), target_(target) {}

  void add(const Key &key, std::string payload) {
    const size_t framed_size = payload.size() + 8;
    if (!payloads_.empty() && estimated_size_ + framed_size > target_)
      flush();
    if (payloads_.empty())
      first_ = key;
    last_ = key;
    estimated_size_ += framed_size;
    payloads_.push_back(std::move(payload));
  }

  std::vector<Meta> finish() {
    flush();
    return std::move(metadata_);
  }

private:
  void flush() {
    if (payloads_.empty())
      return;
    std::ostringstream name;
    name << prefix_ << '-' << std::setfill('0') << std::setw(5) << number_++
         << ".binpb";
    auto bytes = frame(kind_, payloads_);
    write(directory_ / name.str(), bytes);
    metadata_.push_back({name.str(), kind_, bytes.size(), payloads_.size(),
                         sha(bytes), first_, last_});
    payloads_.clear();
    estimated_size_ = 16;
  }

  fs::path directory_;
  std::string prefix_;
  uint32_t kind_;
  size_t target_;
  size_t number_ = 0;
  size_t estimated_size_ = 16;
  Key first_, last_;
  std::vector<std::string> payloads_;
  std::vector<Meta> metadata_;
};
int main(int argc, char **argv) {
  fs::path unpublished_snapshot;
  try {
    if (argc == 3 && std::string(argv[1]) == "--normalize") {
      std::cout << normalize(argv[2]);
      return 0;
    }
    if (argc < 3 || argc > 4) {
      std::cerr << "usage: fox_snapshot_builder CORPUS_ROOT SNAPSHOT_DIR "
                   "[SHARD_BYTES]\n";
      return 2;
    }
    fs::path root = fs::absolute(argv[1]).lexically_normal(),
             out = fs::absolute(argv[2]).lexically_normal();
    size_t target = argc == 4 ? std::stoull(argv[3]) : 4 * 1024 * 1024;
    if (target < 1024)
      throw std::runtime_error("shard size must be at least 1024 bytes");
    if (!fs::is_directory(root))
      throw std::runtime_error("corpus root is not a directory");
    if (fs::exists(out))
      throw std::runtime_error("snapshot destination already exists");
    const auto build_started = Clock::now();
    const auto discovery_started = Clock::now();
    std::vector<std::pair<std::string, fs::path>> files;
    uint64_t corpus_bytes = 0;
    uint64_t skipped_files = 0;
    for (auto const &e : fs::recursive_directory_iterator(root)) {
      if (!e.is_regular_file())
        continue;
      if (e.path().extension() != ".txt") {
        skipped_files++;
        continue;
      }
      auto rel = fs::relative(e.path(), root).generic_string();
      corpus_bytes += e.file_size();
      files.push_back({rel, e.path()});
    }
    std::sort(files.begin(), files.end(),
              [](auto &a, auto &b) { return a.first < b.first; });
    const auto discovery_finished = Clock::now();
    std::map<Key, std::vector<uint32_t>> postings;
    fs::path tmp = out;
    tmp += ".tmp";
    unpublished_snapshot = tmp;
    if (fs::exists(tmp))
      throw std::runtime_error("temporary destination already exists");
    fs::create_directories(tmp);
    ShardWriter record_writer(tmp, "records", 1, target);
    Sha256Stream corpus_identity;
    uint64_t id = 1;
    uint64_t non_empty_normalized_records = 0;
    const auto parsing_started = Clock::now();
    for (auto &[rel, path] : files) {
      std::ifstream f(path, std::ios::binary);
      if (!f)
        throw std::runtime_error("cannot read " + path.string());
      std::string line;
      uint64_t number = 0;
      while (std::getline(f, line)) {
        if (id > std::numeric_limits<uint32_t>::max())
          throw std::runtime_error("sentence count exceeds uint32 posting capacity");
        number++;
        if (!line.empty() && line.back() == '\r')
          line.pop_back();
        if (!valid_utf8(line))
          throw std::runtime_error("invalid UTF-8 at " + rel + ":" +
                                   std::to_string(number));
        auto norm = normalize(line);
        if (!norm.empty())
          non_empty_normalized_records++;
        Record record{id++, number, rel, line, norm};
        record_writer.add({0, ""}, proto_record(record));
        corpus_identity.update_identity_string(rel);
        corpus_identity.update_le64(number);
        corpus_identity.update_identity_string(line);
        add_grams(norm, record.id, postings);
      }
      if (f.bad())
        throw std::runtime_error("read failure " + path.string());
    }
    const auto parsing_finished = Clock::now();
    auto rs = record_writer.finish();

    ShardWriter index_writer(tmp, "index", 2, target);
    const size_t posting_ids_per_chunk = std::max<size_t>(
        1, (std::min<size_t>(target, 8 * 1024 * 1024) - 128) / 10);
    for (const auto &[key, ids] : postings) {
      uint32_t chunk_index = 0;
      for (size_t begin = 0; begin < ids.size();
           begin += posting_ids_per_chunk) {
        const size_t end = std::min(ids.size(), begin + posting_ids_per_chunk);
        index_writer.add(key, proto_posting(key, ids, begin, end, chunk_index++,
                                            end == ids.size()));
      }
    }
    auto is = index_writer.finish();
    std::array<uint64_t, 4> gram_key_counts{};
    uint64_t posting_entries = 0;
    for (const auto &[key, ids] : postings) {
      gram_key_counts[key.first]++;
      posting_entries += ids.size();
    }
    Sha256Stream index_identity;
    for (const auto &[key, ids] : postings) {
      index_identity.update_le64(key.first);
      index_identity.update_identity_string(key.second);
      index_identity.update_le64(ids.size());
      for (uint64_t posting_id : ids)
        index_identity.update_le64(posting_id);
    }
    auto index_digest = index_identity.finish();
    auto digest = corpus_identity.finish();
    auto snapshot_digest =
        sha(digest + index_digest +
            "schema=1;framing=1;normalization=1;index=1;grams=1,2,3;shard=" +
            std::to_string(target));
    write(tmp / "manifest.binpb",
          proto_manifest(id - 1, rs, is, digest, snapshot_digest,
                         index_digest, target));
    fs::rename(tmp, out);
    unpublished_snapshot.clear();
    uint64_t snapshot_bytes = 0;
    for (const auto &entry : fs::directory_iterator(out))
      if (entry.is_regular_file())
        snapshot_bytes += entry.file_size();
    const auto build_finished = Clock::now();
    const uint64_t sentence_count = id - 1;
    const double parsing_seconds =
        std::max(1e-9, std::chrono::duration<double>(parsing_finished - parsing_started).count());
    const double lines_per_second = sentence_count / parsing_seconds;
    std::cout << std::fixed << std::setprecision(2)
              << "[C++ BUILDER] [FILE DISCOVERY] -> Completed in "
              << elapsed_ms(discovery_started, discovery_finished) << " ms | "
              << "Valid Text Files: " << files.size() << " | Corpus Data: "
              << (corpus_bytes / 1000000.0) << " MB | Skipped Files: "
              << skipped_files << ".\n"
              << "[C++ BUILDER] [CORPUS METRICS] -> Extracted "
              << sentence_count << " lines (" << non_empty_normalized_records
              << " non-empty normalized records indexed) | Parsing Time: "
              << elapsed_ms(parsing_started, parsing_finished)
              << " ms | Average Parsing Throughput: " << lines_per_second
              << " lines/sec | Errors: 0.\n"
              << "[C++ BUILDER] [INDEX GENERATION] -> Generated "
              << postings.size() << " distinct N-Grams (1-Gram: "
              << gram_key_counts[1] << ", 2-Gram: " << gram_key_counts[2]
              << ", 3-Gram: " << gram_key_counts[3]
              << ") | Posting Entries: " << posting_entries
              << " | C++ Peak RSS: " << peak_rss_mb()
              << " MB | Posting IDs: uint32 (4 bytes), reusable string_view scratch.\n"
              << "[C++ BUILDER] [SNAPSHOT] -> Published "
              << (snapshot_bytes / 1000000.0) << " MB in "
              << elapsed_ms(build_started, build_finished) << " ms | Record Shards: "
              << rs.size() << " | Index Shards: " << is.size() << ".\n";
    std::cout << "snapshot_id=" << hex(snapshot_digest)
              << " sentences=" << (id - 1) << " files=" << files.size()
              << " record_shards=" << rs.size() << " index_shards=" << is.size()
              << "\n";
    return 0;
  } catch (const std::exception &e) {
    if (!unpublished_snapshot.empty()) {
      std::error_code cleanup_error;
      fs::remove_all(unpublished_snapshot, cleanup_error);
    }
    std::cerr << "error: " << e.what() << "\n";
    return 1;
  }
}
