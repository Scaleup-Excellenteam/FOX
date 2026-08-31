#include <iostream>
#include <fstream>
#include <string>
#include <vector>
#include <unordered_map>
#include <chrono>
#include <cstdint>
#include <cstring>
#include "autocomplete_snapshot.pb.h"

using namespace fox::autocomplete::snapshot::v1;

// פונקציית עזר לפריסת ה-Binary Frames מהשארד
std::vector<std::string> ExtractFrames(const std::string& filepath) {
    std::vector<std::string> payloads;
    std::ifstream file(filepath, std::ios::binary);
    if (!file.is_open()) return payloads;

    // קריאת כל הקובץ לזיכרון
    file.seekg(0, std::ios::end);
    size_t file_size = file.tellg();
    file.seekg(0, std::ios::beg);

    if (file_size < 16) return payloads; // הקובץ קטן מרוחב ה-Header

    std::vector<char> buffer(file_size);
    file.read(buffer.data(), file_size);

    // דילוג על Shard Header (16 Bytes)
    size_t offset = 16;
    while (offset + 4 <= file_size) {
        uint32_t length = 0;
        std::memcpy(&length, buffer.data() + offset, sizeof(uint32_t)); // Little-Endian Unpack
        offset += 4;

        if (offset + length + 4 > file_size) break; // הגנה מגלישת פריים

        payloads.emplace_back(buffer.data() + offset, length);
        offset += length + 4; // דילוג על Payload + 4 Bytes של CRC32C
    }

    return payloads;
}

int main(int argc, char** argv) {
    std::string snapshot_dir = (argc > 1) ? argv[1] : "../../artifacts/snapshot_v1";
    
    SnapshotManifest manifest;
    std::ifstream manifest_file(snapshot_dir + "/manifest.binpb", std::ios::binary);
    
    if (!manifest_file.is_open() || !manifest.ParseFromIstream(&manifest_file)) {
        std::cerr << "Failed to load manifest.binpb from " << snapshot_dir << "\n";
        return 1;
    }

    std::cout << "=== FOX C++ High-Performance Search Engine ===\n";
    std::cout << "Loading Shards into RAM...\n";

    // 1. טעינת הרשומות (Sentence Records)
    std::unordered_map<uint64_t, std::string> sentences;
    for (int i = 0; i < manifest.record_shards_size(); ++i) {
        std::string path = snapshot_dir + "/" + manifest.record_shards(i).file_name();
        auto frames = ExtractFrames(path);
        for (const auto& payload : frames) {
            SentenceRecord rec;
            if (rec.ParseFromString(payload)) {
                sentences[rec.sentence_id()] = rec.original_text();
            }
        }
    }
    std::cout << "[+] Loaded " << sentences.size() << " sentences into RAM.\n";

    // 2. טעינת האינדקס (In-Memory Posting Map for Microsecond Lookups)
    std::unordered_map<std::string, std::vector<uint64_t>> index;
    for (int i = 0; i < manifest.index_shards_size(); ++i) {
        std::string path = snapshot_dir + "/" + manifest.index_shards(i).file_name();
        auto frames = ExtractFrames(path);
        for (const auto& payload : frames) {
            PostingChunk chunk;
            if (chunk.ParseFromString(payload)) {
                auto& list = index[chunk.gram()];
                for (int k = 0; k < chunk.sentence_ids_size(); ++k) {
                    list.push_back(chunk.sentence_ids(k));
                }
            }
        }
    }
    std::cout << "[+] Built In-Memory Index for " << index.size() << " unique n-grams.\n\n";

    // 3. לולאת חיפוש אינטראקטיבית ב-Nanoseconds/Microseconds
    std::string query;
    while (true) {
        std::cout << "Search > ";
        if (!(std::cin >> query) || query == "exit") break;

        auto t0 = std::chrono::high_resolution_clock::now();
        auto it = index.find(query);
        auto elapsed_us = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::high_resolution_clock::now() - t0).count();

        if (it != index.end()) {
            std::cout << "\nFound " << it->second.size() << " matches for '" << query 
                      << "' in " << (elapsed_us / 1000.0) << " ms (" << elapsed_us << " us):\n";

            size_t count = 0;
            for (uint64_t id : it->second) {
                if (++count > 10) break; // 10 תוצאות ראשונות
                std::cout << "  [" << count << "] " << sentences[id] << "\n";
            }
        } else {
            std::cout << "No matches found for '" << query << "'.\n";
        }
        std::cout << "\n";
    }

    return 0;
}
