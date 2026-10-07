#pragma once
// SPDX-License-Identifier: GPL-2.0-or-later
// Future genuine native regression consumer. This source has not been built or run.
#if !defined(__OHOS__) || !defined(__aarch64__) || !defined(__clang__) || __clang_major__ != 20
#  error "This consumer requires genuine OHOS/AArch64 with the reviewed Clang20 profile"
#endif
#if __cplusplus < 201703L
#  error "C++17 is required"
#endif
#define ZDICT_STATIC_LINKING_ONLY
#include <zdict.h>
#include <zstd.h>
#include <algorithm>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <exception>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <sys/utsname.h>
#if !defined(_LIBCPP_VERSION) || _LIBCPP_VERSION != 15004
#  error "Use the reviewed SDK15 libc++15004 headers"
#endif
#define COVER_STRINGIFY_INNER(x) #x
#define COVER_STRINGIFY(x) COVER_STRINGIFY_INNER(x)
constexpr bool sameLiteral(const char* a, const char* b) {
    return *a == *b && (*a == '\0' || sameLiteral(a + 1, b + 1));
}
static_assert(sameLiteral(COVER_STRINGIFY(_LIBCPP_ABI_NAMESPACE), "__n1"),
              "Use the reviewed SDK15 std::__n1 ABI");
static_assert(ZSTD_VERSION_NUMBER == 10507, "Pinned Zstd1.5.7 headers required");

namespace base_cover_regression {
using Bytes = std::vector<unsigned char>;
constexpr unsigned sampleCount = 256;
constexpr size_t sampleSize = 1024;
constexpr size_t dictCapacity = 2048;
constexpr size_t largeMessageSize = 2 * 1024 * 1024;
struct Options {
    unsigned workers = 2;
    unsigned iterations = 6;
    unsigned codecWorkers = 2;
    unsigned optimizerThreads = 2;
    unsigned notificationLevel = 0;
};
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
void zcheck(size_t result, const char* operation) {
    if (ZSTD_isError(result)) {
        throw std::runtime_error(std::string(operation) + ": " + ZSTD_getErrorName(result));
    }
}
void dcheck(size_t result, const char* operation) {
    if (ZDICT_isError(result)) {
        throw std::runtime_error(std::string(operation) + ": " + ZDICT_getErrorName(result));
    }
}
// A cancelled gate releases peers if any worker fails or thread creation throws.
class Gate {
    std::mutex mutex;
    std::condition_variable condition;
    const unsigned required;
    unsigned arrived = 0;
    unsigned generation = 0;
    bool cancelled = false;
public:
    explicit Gate(unsigned count) : required(count) {}
    void wait() {
        std::unique_lock<std::mutex> lock(mutex);
        if (cancelled) throw std::runtime_error("peer cancelled concurrent run");
        const unsigned oldGeneration = generation;
        if (++arrived == required) {
            arrived = 0;
            ++generation;
            condition.notify_all();
        } else {
            condition.wait(lock, [&] { return cancelled || generation != oldGeneration; });
        }
        if (cancelled) throw std::runtime_error("peer cancelled concurrent run");
    }
    void cancel() {
        std::lock_guard<std::mutex> lock(mutex);
        cancelled = true;
        condition.notify_all();
    }
};
struct Corpus {
    Bytes samples;
    std::vector<size_t> sizes;
    Bytes heldOut;
};
// Distinct repeated vocabulary per trainer plus changing record fields and noise.
// All buffers are independent; no downloaded or binary fixtures are involved.
void sample(Bytes& output, unsigned owner, unsigned record) {
    const char* vocabulary[] = {
        "camera transform exposure mesh vertex material texture scene ",
        "dictionary suffix frequency segment trainer compressor frame ",
        "animation timeline keyframe channel render shader geometry ",
        "document paragraph heading glyph font layout language sample "
    };
    const char* words = vocabulary[owner % 4];
    const size_t wordsSize = std::strlen(words);
    unsigned noise = 0x9e3779b9U ^ (owner * 0x85ebca6bU) ^ record;
    for (size_t offset = 0; offset < sampleSize; ++offset) {
        noise ^= noise << 13;
        noise ^= noise >> 17;
        noise ^= noise << 5;
        unsigned char byte = static_cast<unsigned char>(words[(offset + (record % 7)) % wordsSize]);
        if (offset % 64 < 8) byte = static_cast<unsigned char>('0' + ((record + owner + offset) % 10));
        if (offset % 41 == 0) byte = static_cast<unsigned char>(noise & 0xffU);
        output.push_back(byte);
    }
}
Corpus corpus(unsigned owner) {
    Corpus result;
    result.samples.reserve(sampleCount * sampleSize);
    result.sizes.assign(sampleCount, sampleSize);
    for (unsigned i = 0; i < sampleCount; ++i) sample(result.samples, owner, i);
    sample(result.heldOut, owner, sampleCount + 17);
    return result;
}
ZDICT_cover_params_t parameters(unsigned owner, unsigned d, unsigned level) {
    ZDICT_cover_params_t result{};
    result.k = 128;
    result.d = d;
    result.zParams.compressionLevel = 3;
    result.zParams.notificationLevel = level;
    result.zParams.dictID = 0x432100U + owner;
    return result;
}
Bytes train(const Corpus& data, unsigned owner, unsigned d, unsigned level) {
    Bytes dictionary(dictCapacity);
    const size_t n = ZDICT_trainFromBuffer_cover(dictionary.data(), dictionary.size(),
        data.samples.data(), data.sizes.data(), sampleCount, parameters(owner, d, level));
    dcheck(n, "ZDICT_trainFromBuffer_cover");
    require(n >= ZDICT_DICTSIZE_MIN && n <= dictionary.size(), "invalid COVER dictionary size");
    dictionary.resize(n);
    require(ZDICT_getDictID(dictionary.data(), n) == 0x432100U + owner, "wrong COVER dictionary id");
    const size_t header = ZDICT_getDictHeaderSize(dictionary.data(), n);
    dcheck(header, "ZDICT_getDictHeaderSize");
    require(header > 0 && header < n, "invalid dictionary header/content");
    return dictionary;
}
using CCtx = std::unique_ptr<ZSTD_CCtx, decltype(&ZSTD_freeCCtx)>;
using DCtx = std::unique_ptr<ZSTD_DCtx, decltype(&ZSTD_freeDCtx)>;
size_t roundTrip(const Bytes& dictionary, const Bytes& message, unsigned codecWorkers) {
    CCtx compressor(ZSTD_createCCtx(), ZSTD_freeCCtx);
    DCtx decompressor(ZSTD_createDCtx(), ZSTD_freeDCtx);
    require(bool(compressor) && bool(decompressor), "codec context allocation");
    zcheck(ZSTD_CCtx_setParameter(compressor.get(), ZSTD_c_compressionLevel, 3), "set compression level");
    zcheck(ZSTD_CCtx_setParameter(compressor.get(), ZSTD_c_nbWorkers, static_cast<int>(codecWorkers)),
           "set native codec workers");
    zcheck(ZSTD_CCtx_loadDictionary(compressor.get(), dictionary.data(), dictionary.size()), "load trained dictionary");
    Bytes compressed(ZSTD_compressBound(message.size()));
    const size_t n = ZSTD_compress2(compressor.get(), compressed.data(), compressed.size(), message.data(), message.size());
    zcheck(n, "compress with trained dictionary");
    require(n < message.size(), "generated corpus was not compressed");
    require(ZSTD_getDictID_fromFrame(compressed.data(), n) == ZDICT_getDictID(dictionary.data(), dictionary.size()),
            "frame does not select the trained dictionary");
    Bytes decoded(message.size());
    const size_t out = ZSTD_decompress_usingDict(decompressor.get(), decoded.data(), decoded.size(),
        compressed.data(), n, dictionary.data(), dictionary.size());
    zcheck(out, "decompress with trained dictionary");
    require(out == message.size() && decoded == message, "dictionary roundtrip mismatch");
    // The full dictionary has a nonzero id, so decoding without it must fail.
    require(ZSTD_isError(ZSTD_decompress(decoded.data(), decoded.size(), compressed.data(), n)),
            "dictionary frame unexpectedly decoded without its dictionary");
    compressed[0] ^= 0xffU;
    require(ZSTD_isError(ZSTD_decompress_usingDict(decompressor.get(), decoded.data(), decoded.size(),
        compressed.data(), n, dictionary.data(), dictionary.size())), "corrupt magic accepted");
    return n;
}
} // namespace base_cover_regression

// Shared helper bodies above are copied from the separately sealed standalone
// regression consumer. This header is included only by bridge.cc.
#include "base_cover_api.h"
#include <array>
#include <atomic>
#include <cerrno>

namespace base_cover_regression {
class ModuleOwner {
    static constexpr unsigned externalCount = 2;
    static constexpr unsigned internalCount = 2;
    static constexpr unsigned workerCount = externalCount + internalCount;
    unsigned iterations;
    unsigned codecWorkers;
    unsigned notificationLevel;
    Gate gate{workerCount};
    std::array<Corpus, workerCount> inputs;
    std::array<Bytes, workerCount> references;
    std::array<unsigned, workerCount> completed{};
    std::array<unsigned, workerCount> optimizedD{};
    std::array<int, workerCount> failures{};
    std::array<uintptr_t, workerCount> errnoAddresses{};
    std::array<std::atomic<bool>, workerCount> entered{};
    std::array<std::thread, internalCount> internal;
    bool started = false;
    bool finished = false;
public:
    ModuleOwner(unsigned count, unsigned codecs, unsigned level)
        : iterations(count), codecWorkers(codecs), notificationLevel(level) {
        require(count > 0 && count <= 8 && codecs > 0 && codecs <= 2 && level <= 4,
                "invalid module regression parameters");
        require(std::strcmp(ZSTD_versionString(), "1.5.7") == 0, "module linked Zstd version mismatch");
        for (unsigned i = 0; i < workerCount; ++i) {
            entered[i].store(false);
            inputs[i] = corpus(i + 16);
            // Prepare serial references in the module before releasing workers.
            references[i] = train(inputs[i], i + 16, d(i), 0);
            roundTrip(references[i], inputs[i].heldOut, 0);
        }
    }
    ~ModuleOwner() {
        gate.cancel();
        for (auto& thread : internal) if (thread.joinable()) thread.join();
    }
    static unsigned d(unsigned index) { return index % 2 == 0 ? 8 : 12; }
    void cancel() { gate.cancel(); }
    int run(unsigned index, uintptr_t callerErrnoAddress) {
        if (index >= workerCount) { gate.cancel(); return 1; }
        if (entered[index].exchange(true)) { gate.cancel(); return 1; }
        try {
            // All four threads are alive at the first gate. Record addresses
            // before any thread can finish; no errno value preservation claim.
            errnoAddresses[index] = reinterpret_cast<uintptr_t>(&errno);
            require(errnoAddresses[index] != 0, "module errno accessor returned null");
            if (index < externalCount) {
                require(errnoAddresses[index] == callerErrnoAddress,
                        "driver/module errno accessors disagree on the same thread");
            }
            for (unsigned iteration = 0; iteration < iterations; ++iteration) {
                gate.wait();
                const Bytes dictionary = train(inputs[index], index + 16, d(index), notificationLevel);
                require(dictionary == references[index], "module concurrent dictionary differs from serial reference");
                roundTrip(dictionary, inputs[index].heldOut, 0);
                Bytes large;
                large.reserve(largeMessageSize);
                while (large.size() < largeMessageSize) {
                    large.insert(large.end(), inputs[index].heldOut.begin(), inputs[index].heldOut.end());
                }
                roundTrip(dictionary, large, codecWorkers);
                ++completed[index];
            }
            gate.wait();
            auto p = parameters(index + 16, d(index), notificationLevel);
            p.k = 0;
            p.steps = 2;
            p.nbThreads = 2;
            Bytes optimized(dictCapacity);
            const size_t size = ZDICT_optimizeTrainFromBuffer_cover(optimized.data(), optimized.size(),
                inputs[index].samples.data(), inputs[index].sizes.data(), sampleCount, &p);
            dcheck(size, "module ZDICT_optimizeTrainFromBuffer_cover");
            require(size >= ZDICT_DICTSIZE_MIN && size <= optimized.size() && p.d == d(index),
                    "invalid module optimized dictionary");
            optimized.resize(size);
            require(ZDICT_getDictID(optimized.data(), size) == 0x432100U + index + 16,
                    "wrong module optimized dictionary id");
            roundTrip(optimized, inputs[index].heldOut, 0);
            optimizedD[index] = p.d;
            return 0;
        } catch (const std::exception& error) {
            failures[index] = 1;
            std::fprintf(stderr, "FAIL module COVER worker=%u d=%u: %s\n", index, d(index), error.what());
            gate.cancel();
            return 1;
        } catch (...) {
            failures[index] = 1;
            gate.cancel();
            return 1;
        }
    }
    int startInternal() {
        if (started) return 1;
        started = true;
        try {
            for (unsigned i = 0; i < internalCount; ++i) {
                internal[i] = std::thread([this, i] { run(i + externalCount, 0); });
            }
            return 0;
        } catch (...) {
            gate.cancel();
            for (auto& thread : internal) if (thread.joinable()) thread.join();
            return 1;
        }
    }
    int finish() {
        if (!started || finished) return 1;
        for (auto& thread : internal) if (thread.joinable()) thread.join();
        finished = true;
        for (unsigned i = 0; i < workerCount; ++i) {
            if (!entered[i].load() || failures[i] || completed[i] != iterations || optimizedD[i] != d(i)) return 1;
            for (unsigned j = 0; j < i; ++j) if (errnoAddresses[i] == errnoAddresses[j]) return 1;
        }
        std::printf("module COVER workers=4 preexisting=2 internal=2 iterations=%u; branches=d8,d12; "
                    "serial-dictionary+roundtrip+optimizer checks; distinct errno addresses=4\n", iterations);
        return 0;
    }
};
} // namespace base_cover_regression

extern "C" void *base_cover_create(unsigned iterations, unsigned codec_workers, unsigned notification_level) {
    try { return new base_cover_regression::ModuleOwner(iterations, codec_workers, notification_level); }
    catch (const std::exception& error) {
        std::fprintf(stderr, "FAIL module COVER create: %s\n", error.what());
        return nullptr;
    } catch (...) { return nullptr; }
}
extern "C" int base_cover_start_internal(void *handle) {
    if (!handle) return 1;
    try { return static_cast<base_cover_regression::ModuleOwner*>(handle)->startInternal(); }
    catch (...) { return 1; }
}
extern "C" int base_cover_external(void *handle, unsigned index, uintptr_t driver_errno_address) {
    if (!handle) return 1;
    try {
        auto* owner = static_cast<base_cover_regression::ModuleOwner*>(handle);
        if (index >= 2) { owner->cancel(); return 1; }
        return owner->run(index, driver_errno_address);
    }
    catch (...) { return 1; }
}
extern "C" int base_cover_finish(void *handle) {
    if (!handle) return 1;
    try { return static_cast<base_cover_regression::ModuleOwner*>(handle)->finish(); }
    catch (...) { return 1; }
}
extern "C" int base_cover_destroy(void *handle) {
    try { delete static_cast<base_cover_regression::ModuleOwner*>(handle); return 0; }
    catch (...) { return 1; }
}
