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

namespace {
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
struct Result {
    unsigned completed = 0;
    unsigned optimizedD = 0;
    size_t dictSize = 0;
    size_t largeCompressed = 0;
    std::string failure;
};
unsigned number(const char* text, unsigned minimum, unsigned maximum) {
    require(*text != '\0', "missing numeric option");
    unsigned value = 0;
    for (; *text; ++text) {
        require(*text >= '0' && *text <= '9', "numeric options require decimal digits");
        value = value * 10 + static_cast<unsigned>(*text - '0');
        require(value <= maximum, "numeric option exceeds maximum");
    }
    require(value >= minimum, "numeric option below minimum");
    return value;
}
Options options(int argc, char** argv) {
    Options result;
    for (int i = 1; i < argc; ++i) {
        const std::string arg(argv[i]);
        if (arg.compare(0, 10, "--workers=") == 0) result.workers = number(argv[i] + 10, 2, 8);
        else if (arg.compare(0, 13, "--iterations=") == 0) result.iterations = number(argv[i] + 13, 1, 100);
        else if (arg.compare(0, 16, "--codec-workers=") == 0) result.codecWorkers = number(argv[i] + 16, 1, 2);
        else if (arg.compare(0, 20, "--optimizer-threads=") == 0) result.optimizerThreads = number(argv[i] + 20, 1, 2);
        else if (arg.compare(0, 21, "--notification-level=") == 0) result.notificationLevel = number(argv[i] + 21, 0, 4);
        else throw std::runtime_error("unknown option; use --help for the CLI");
    }
    return result;
}
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--help") == 0) {
        std::puts("zstd-cover-concurrent [--workers=2..8] [--iterations=1..100] [--codec-workers=1..2] "
                  "[--optimizer-threads=1..2] [--notification-level=0..4]\n"
                  "Defaults: workers=2 iterations=6 codec-workers=2 optimizer-threads=2 notification-level=0\n"
                  "Worker d values cycle 8,12,6. Use notification-level=2/3 for future diagnostic observation.");
        return 0;
    }
    try {
        const Options opts = options(argc, argv);
        struct utsname host{};
        require(uname(&host) == 0 && std::strcmp(host.sysname, "HarmonyOS") == 0 &&
                std::strcmp(host.machine, "aarch64") == 0, "actual native HarmonyOS/aarch64 host required");
        require(std::strcmp(ZSTD_versionString(), "1.5.7") == 0, "linked Zstd version mismatch");
        const unsigned dValues[] = {8, 12, 6};
        std::vector<Corpus> inputs;
        std::vector<Bytes> references;
        for (unsigned i = 0; i < opts.workers; ++i) {
            inputs.push_back(corpus(i));
            references.push_back(train(inputs.back(), i, dValues[i % 3], 0));
            roundTrip(references.back(), inputs.back().heldOut, 0);
        }
        Gate start(opts.workers);
        std::vector<Result> results(opts.workers);
        std::vector<std::thread> threads;
        threads.reserve(opts.workers);
        try {
            for (unsigned i = 0; i < opts.workers; ++i) {
                threads.emplace_back([&, i] {
                    try {
                        for (unsigned iteration = 0; iteration < opts.iterations; ++iteration) {
                            // Release independent real trainer calls together on every iteration.
                            start.wait();
                            const Bytes dictionary = train(inputs[i], i, dValues[i % 3], opts.notificationLevel);
                            require(dictionary == references[i], "concurrent dictionary differs from serial reference");
                            roundTrip(dictionary, inputs[i].heldOut, 0);
                            Bytes large;
                            large.reserve(largeMessageSize);
                            while (large.size() < largeMessageSize) {
                                large.insert(large.end(), inputs[i].heldOut.begin(), inputs[i].heldOut.end());
                            }
                            results[i].largeCompressed = roundTrip(dictionary, large, opts.codecWorkers);
                            results[i].dictSize = dictionary.size();
                            ++results[i].completed;
                        }
                        // Exercise genuine COVER pool jobs. Sorting still occurs in each caller;
                        // optimizer worker threads reuse that caller's immutable sorted context.
                        start.wait();
                        auto p = parameters(i, dValues[i % 3], opts.notificationLevel);
                        p.k = 0;
                        p.steps = 2;
                        p.nbThreads = opts.optimizerThreads;
                        Bytes optimized(dictCapacity);
                        const size_t n = ZDICT_optimizeTrainFromBuffer_cover(optimized.data(), optimized.size(),
                            inputs[i].samples.data(), inputs[i].sizes.data(), sampleCount, &p);
                        dcheck(n, "ZDICT_optimizeTrainFromBuffer_cover");
                        require(n >= ZDICT_DICTSIZE_MIN && n <= optimized.size(), "invalid optimized dictionary size");
                        require(p.d == dValues[i % 3], "optimizer changed fixed d branch");
                        optimized.resize(n);
                        require(ZDICT_getDictID(optimized.data(), n) == 0x432100U + i, "wrong optimized dictionary id");
                        roundTrip(optimized, inputs[i].heldOut, 0);
                        results[i].optimizedD = p.d;
                    } catch (const std::exception& error) {
                        results[i].failure = error.what();
                        start.cancel();
                    } catch (...) {
                        results[i].failure = "unknown worker exception";
                        start.cancel();
                    }
                });
            }
        } catch (...) {
            start.cancel();
            for (auto& thread : threads) thread.join();
            throw;
        }
        for (auto& thread : threads) thread.join();
        bool failed = false;
        for (unsigned i = 0; i < opts.workers; ++i) {
            const Result& r = results[i];
            if (!r.failure.empty()) {
                std::fprintf(stderr, "FAIL worker=%u d=%u completed=%u: %s\n", i, dValues[i % 3], r.completed, r.failure.c_str());
                failed = true;
            } else {
                require(r.completed == opts.iterations && r.optimizedD == dValues[i % 3], "incomplete worker results");
                std::printf("worker=%u d=%u iterations=%u dictionary=%zu large_compressed=%zu optimizer_d=%u\n",
                    i, dValues[i % 3], r.completed, r.dictSize, r.largeCompressed, r.optimizedD);
            }
        }
        if (failed) return 1;
        std::printf("PASS actual COVER trainers=%u branches=d<=8,d>8 iterations=%u; exact serial dictionary comparison; "
                    "trained-dictionary small+2MiB roundtrips; codec_workers_requested=%u optimizer_threads_requested=%u; "
                    "missing-dictionary+corrupt-frame rejection\n", opts.workers, opts.iterations, opts.codecWorkers, opts.optimizerThreads);
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "FAIL zstd-cover-concurrent: %s\n", error.what());
        return 1;
    }
}
