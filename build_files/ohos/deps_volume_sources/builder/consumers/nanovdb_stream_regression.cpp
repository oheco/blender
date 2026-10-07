// Copyright 2026 oheco contributors
// SPDX-License-Identifier: Apache-2.0
#include <nanovdb/io/IO.h>
#include <nanovdb/tools/CreatePrimitives.h>
#include <oneapi/tbb/global_control.h>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

static int checks = 0;
static void require(bool ok, const char* text) {
    ++checks;
    if (!ok) throw std::runtime_error(text);
}
template<class F> static void rejects(F callback, const char* text) {
    bool rejected = false;
    try { callback(); } catch (const std::exception&) { rejected = true; }
    require(rejected, text);
}
using Buffer = nanovdb::HostBuffer;
static void invalid(const std::string& bytes) {
    for (int mode = 0; mode != 3; ++mode) {
        std::istringstream stream(bytes);
        std::cerr << "TRACE invalid length=" << bytes.size() << " mode=" << mode << '\n';
        rejects([&] {
            if (mode == 0) (void)nanovdb::io::readGrid<Buffer>(stream, 0, Buffer());
            if (mode == 1) (void)nanovdb::io::readGrid<Buffer>(stream, -1, Buffer());
            if (mode == 2) (void)nanovdb::io::readGrid<Buffer>(stream, std::string("absent"), Buffer());
        }, "invalid real Nano stream was not rejected");
    }
}
static void probe_restore() {
    for (int mode = 0; mode != 3; ++mode) {
        std::istringstream stream("prefix-invalid NanoVDB fixture");
        stream.seekg(7);
        nanovdb::GridHandle<Buffer> handle;
        bool fallback = false;
        try {
            if (mode == 0) handle.read(stream, Buffer());
            if (mode == 1) handle.read(stream, uint32_t(0), Buffer());
            if (mode == 2) handle.read(stream, std::string("absent"), Buffer());
        } catch (const std::logic_error&) { fallback = true; }
        require(fallback && bool(stream) && stream.tellg() == std::streampos(7), "raw probe did not restore its original stream offset/state");
    }
}
static void valid_roundtrips() {
    auto original = nanovdb::tools::createLevelSetSphere<float>(1.0, nanovdb::Vec3d(0), .5, 3.0, nanovdb::Vec3d(0), "stream fixture");
    const auto* grid = original.grid<float>();
    require(grid != nullptr, "real Nano grid missing");
    for (auto codec : {nanovdb::io::Codec::NONE, nanovdb::io::Codec::ZIP, nanovdb::io::Codec::BLOSC}) {
        std::ostringstream out(std::ios::out | std::ios::binary);
        nanovdb::io::writeGrid(out, original, codec);
        const auto bytes = out.str();
        for (int mode = 0; mode != 3; ++mode) {
            std::istringstream in(bytes, std::ios::in | std::ios::binary);
            auto read = mode == 2 ? nanovdb::io::readGrid<Buffer>(in, std::string("stream fixture"), Buffer())
                                  : nanovdb::io::readGrid<Buffer>(in, mode == 0 ? 0 : -1, Buffer());
            const auto* decoded = read.grid<float>();
            require(decoded && decoded->activeVoxelCount() == grid->activeVoxelCount(), "valid segmented grid topology changed");
            for (int x = -2; x <= 2; ++x) for (int y = -2; y <= 2; ++y) for (int z = -2; z <= 2; ++z) {
                auto coord = nanovdb::Coord(x, y, z);
                require(decoded->tree().getValue(coord) == grid->tree().getValue(coord), "valid segmented grid values changed");
            }
        }
    }
    std::ostringstream raw(std::ios::out | std::ios::binary);
    original.write(raw);
    for (int mode = 0; mode != 3; ++mode) {
        std::istringstream in(raw.str(), std::ios::in | std::ios::binary);
        auto read = mode == 2 ? nanovdb::io::readGrid<Buffer>(in, std::string("stream fixture"), Buffer())
                              : nanovdb::io::readGrid<Buffer>(in, mode == 0 ? 0 : -1, Buffer());
        require(read.grid<float>() && read.grid<float>()->activeVoxelCount() == grid->activeVoxelCount(), "valid raw grid changed");
    }
}
int main() {
    try {
        oneapi::tbb::global_control threads(oneapi::tbb::global_control::max_allowed_parallelism, 2);
        invalid("invalid NanoVDB fixture");
        for (size_t size : {size_t(0), size_t(1), size_t(7), size_t(15), size_t(sizeof(nanovdb::GridData) - 1), size_t(sizeof(nanovdb::GridData) + 32)}) invalid(std::string(size, '?'));
        probe_restore();
        {
            nanovdb::io::Segment segment;
            std::istringstream empty("");
            require(!segment.read(empty), "normal segment EOF changed");
            std::istringstream failed("unused");
            failed.setstate(std::ios::failbit);
            rejects([&]{ (void)segment.read(failed); }, "failed stream accepted a default segment header");
        }
        valid_roundtrips();
        std::cout << "ALL PASS NanoVDB stream regression checks=" << checks << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL Nano stream checks=" << checks << ": " << error.what() << '\n';
        return 1;
    }
}
