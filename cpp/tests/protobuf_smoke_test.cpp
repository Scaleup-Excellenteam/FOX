#include <cstdint>
#include <string>

#include "autocomplete_snapshot.pb.h"

int main() {
  autocomplete::snapshot::v1::SnapshotManifestProto manifest;
  manifest.set_schema_version(1);
  manifest.set_normalization_version(1);
  manifest.set_index_strategy_version(1);
  manifest.add_gram_sizes(1);
  manifest.add_gram_sizes(2);
  manifest.add_gram_sizes(3);

  std::string serialized;
  if (!manifest.SerializeToString(&serialized)) {
    return 1;
  }

  autocomplete::snapshot::v1::SnapshotManifestProto parsed;
  if (!parsed.ParseFromString(serialized)) {
    return 2;
  }

  if (parsed.schema_version() != 1 || parsed.normalization_version() != 1 ||
      parsed.index_strategy_version() != 1 || parsed.gram_sizes_size() != 3 ||
      parsed.gram_sizes(0) != 1 || parsed.gram_sizes(1) != 2 ||
      parsed.gram_sizes(2) != 3) {
    return 3;
  }

  return 0;
}
