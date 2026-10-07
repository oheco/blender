// SPDX-License-Identifier: GPL-2.0-or-later
// Driver owns its two threads; all training allocations/exceptions stay in the DSO.
#include "base_cover_api.h"
#include <array>
#include <cerrno>
#include <condition_variable>
#include <cstdio>
#include <dlfcn.h>
#include <mutex>
#include <stdexcept>
#include <thread>

namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
template<class Function> Function symbol(void* module, const char* name) {
    auto function = reinterpret_cast<Function>(dlsym(module, name));
    if (!function) throw std::runtime_error(name);
    return function;
}
class PreexistingCallers {
    std::mutex mutex;
    std::condition_variable condition;
    std::array<std::thread, 2> threads;
    std::array<int, 2> results{{1, 1}};
    unsigned ready = 0;
    bool released = false;
    bool aborted = false;
    decltype(&base_cover_external) entry = nullptr;
    void* owner = nullptr;
public:
    PreexistingCallers() {
        try {
            for (unsigned i = 0; i < threads.size(); ++i) {
                threads[i] = std::thread([this, i] {
                    const uintptr_t callerErrno = reinterpret_cast<uintptr_t>(&errno);
                    decltype(&base_cover_external) call = nullptr;
                    void* handle = nullptr;
                    {
                        std::unique_lock<std::mutex> lock(mutex);
                        ++ready;
                        condition.notify_all();
                        condition.wait(lock, [this] { return released || aborted; });
                        if (aborted) return;
                        call = entry;
                        handle = owner;
                    }
                    // Entry catches all module exceptions and returns an integer.
                    results[i] = call(handle, i, callerErrno);
                });
            }
        } catch (...) {
            abort();
            join();
            throw;
        }
    }
    ~PreexistingCallers() { abort(); join(); }
    void waitReady() {
        std::unique_lock<std::mutex> lock(mutex);
        condition.wait(lock, [this] { return ready == threads.size(); });
    }
    void release(decltype(&base_cover_external) call, void* handle) {
        std::lock_guard<std::mutex> lock(mutex);
        entry = call;
        owner = handle;
        released = true;
        condition.notify_all();
    }
    void abort() {
        std::lock_guard<std::mutex> lock(mutex);
        aborted = true;
        condition.notify_all();
    }
    void join() {
        for (auto& thread : threads) if (thread.joinable()) thread.join();
    }
    bool succeeded() const { return results[0] == 0 && results[1] == 0; }
};
} // namespace

int main(int argc, char** argv) {
    if (argc != 3) return 2;
    try {
        // Both external threads reach their wait before the very first dlopen.
        PreexistingCallers callers;
        callers.waitReady();
        void* module = nullptr;
        void* coverOwner = nullptr;
        decltype(&base_cover_destroy) coverDestroy = nullptr;
        int result = 1;
        try {
            module = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
            if (!module) {
                std::fprintf(stderr, "dlopen: %s\n", dlerror());
                throw std::runtime_error("module load");
            }
            using Create = void*(*)(const char*);
            using Check = int(*)(void*);
            using Destroy = void(*)(void*);
            const auto create = symbol<Create>(module, "base_create");
            const auto check = symbol<Check>(module, "base_check");
            const auto destroy = symbol<Destroy>(module, "base_destroy");
            const auto coverCreate = symbol<decltype(&base_cover_create)>(module, "base_cover_create");
            const auto coverStart = symbol<decltype(&base_cover_start_internal)>(module, "base_cover_start_internal");
            const auto coverExternal = symbol<decltype(&base_cover_external)>(module, "base_cover_external");
            const auto coverFinish = symbol<decltype(&base_cover_finish)>(module, "base_cover_finish");
            coverDestroy = symbol<decltype(&base_cover_destroy)>(module, "base_cover_destroy");
            for (unsigned i = 0; i < 4; ++i) {
                void* owner = create(argv[2]);
                require(owner != nullptr, "module image/font owner creation");
                const int checked = check(owner);
                destroy(owner);
                require(checked == 0, "module image/text/math owner check");
            }
            // Four independent corpora and serial references are module-owned.
            coverOwner = coverCreate(2, 2, 3);
            require(coverOwner != nullptr, "module COVER owner creation");
            // These two native workers are created by the already-loaded module.
            require(coverStart(coverOwner) == 0, "module internal COVER worker creation");
            callers.release(coverExternal, coverOwner);
            callers.join();
            require(callers.succeeded(), "pre-dlopen caller COVER regression");
            require(coverFinish(coverOwner) == 0, "module internal COVER joins/results/errno isolation");
            const int destroyed = coverDestroy(coverOwner);
            coverOwner = nullptr;
            require(destroyed == 0, "module COVER owner destruction");
            result = 0;
        } catch (const std::exception& error) {
            std::fprintf(stderr, "FAIL module-owner: %s\n", error.what());
        } catch (...) {
            std::fprintf(stderr, "FAIL module-owner: unknown driver exception\n");
        }
        // Cleanup also handles symbol/creation failures. Keep the module mapped
        // until external calls stop and module-owned destructor joins internals.
        callers.abort();
        callers.join();
        if (coverOwner && coverDestroy && coverDestroy(coverOwner) != 0) result = 1;
        if (module && dlclose(module) != 0) result = 1;
        if (result == 0) {
            std::puts("PASS signed whole-archive static image/text/math MODULE dlopen/dlsym; opaque module-owned "
                      "create/check/destroy four iterations; no C++ objects/exceptions cross ABI");
            std::puts("PASS MODULE COVER preexisting=2 internal=2 branches=d<=8,d>8 iterations=2; "
                      "serial dictionaries+trained roundtrips+optimizer; driver/module errno accessors match; "
                      "four thread errno addresses distinct; all workers joined and owners destroyed before dlclose");
        }
        return result;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "FAIL module-owner setup: %s\n", error.what());
        return 1;
    }
}
