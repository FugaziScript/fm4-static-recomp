// Converts the SDK's Vulkan pipeline storage (4D530910.fbo.vk.xpso) written by a Windows build to
// the layout an Android (or macOS) build reads, so the Android game can build the whole pipeline
// list at startup as the Windows game does.
//
// PipelineDescription (shiftglue-sdk pipeline_cache.h) is a packed struct of 32-bit bit fields.
// The Microsoft ABI starts a new 32-bit unit where a field would cross one, so the stencil back
// face fields begin a second unit: 60 bytes. The Itanium ABI of Android and macOS packs them
// straight on: 58 bytes. Only those 15 bits move; the record hash (XXH3 over the description) is
// computed again.
//
// Build: clang++ -std=c++20 -O2 -I<shiftglue-sdk>/thirdparty/xxHash pipeline_list_to_android.cpp
// Usage: pipeline_list_to_android <windows .xpso> <android .xpso>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

#define XXH_INLINE_ALL
#include "xxhash.h"

namespace {

constexpr size_t kHeaderSize = 12;
constexpr size_t kWindowsDescription = 60;
constexpr size_t kAndroidDescription = 58;

bool ReadFile(const char* path, std::vector<uint8_t>& data) {
  FILE* file = std::fopen(path, "rb");
  if (!file) return false;
  uint8_t buffer[1 << 16];
  for (size_t read; (read = std::fread(buffer, 1, sizeof(buffer), file)) != 0;) {
    data.insert(data.end(), buffer, buffer + read);
  }
  std::fclose(file);
  return true;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 3) {
    std::fprintf(stderr, "usage: %s <windows .xpso> <android .xpso>\n", argv[0]);
    return 2;
  }
  std::vector<uint8_t> in;
  if (!ReadFile(argv[1], in) || in.size() < kHeaderSize) {
    std::fprintf(stderr, "cannot read %s\n", argv[1]);
    return 1;
  }
  uint32_t magic;
  std::memcpy(&magic, in.data(), 4);
  if (magic != 0x53504558) {  // 'XEPS'
    std::fprintf(stderr, "%s is not a pipeline storage file\n", argv[1]);
    return 1;
  }
  std::vector<uint8_t> out(in.begin(), in.begin() + kHeaderSize);
  size_t converted = 0, bad = 0;
  for (size_t offset = kHeaderSize; offset + 8 + kWindowsDescription <= in.size();
       offset += 8 + kWindowsDescription) {
    const uint8_t* record = in.data() + offset;
    uint64_t hash;
    std::memcpy(&hash, record, 8);
    const uint8_t* description = record + 8;
    // The SDK stops at the first record whose hash does not match: so does this.
    if (XXH3_64bits(description, kWindowsDescription) != hash) {
      ++bad;
      break;
    }
    // Bytes 0-39: the shader hashes and modifications, the render pass key and the first
    // 32-bit unit of the bit fields, the same in both layouts. 40-43: the second unit, of which
    // 15 bits are used; 44-59: the four render targets, 4 bytes each in both.
    uint32_t second_unit;
    std::memcpy(&second_unit, description + 40, 4);
    if (second_unit >> 15) {
      std::fprintf(stderr, "record at %zu uses bits the Android layout has no room for\n", offset);
      return 1;
    }
    uint8_t android[kAndroidDescription];
    std::memcpy(android, description, 40);
    android[40] = uint8_t(second_unit);
    android[41] = uint8_t(second_unit >> 8);
    std::memcpy(android + 42, description + 44, 16);
    const uint64_t android_hash = XXH3_64bits(android, kAndroidDescription);
    out.insert(out.end(), reinterpret_cast<const uint8_t*>(&android_hash),
               reinterpret_cast<const uint8_t*>(&android_hash) + 8);
    out.insert(out.end(), android, android + kAndroidDescription);
    ++converted;
  }
  FILE* file = std::fopen(argv[2], "wb");
  if (!file || std::fwrite(out.data(), 1, out.size(), file) != out.size()) {
    std::fprintf(stderr, "cannot write %s\n", argv[2]);
    return 1;
  }
  std::fclose(file);
  std::printf("%zu pipelines converted%s\n", converted,
              bad ? ", stopped at a record with a wrong hash" : "");
  return 0;
}
