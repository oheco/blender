# SPDX-License-Identifier: Apache-2.0
"""Load the actual Blender bridge DSOs, check their C ABI, and generate compressed glTF.

Run only after the parent builder has linked, signed and staged the bridge libraries.
This script never builds libraries and uses neither numpy nor bpy.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import ctypes as C
import json
import os
from pathlib import Path
import struct
import sys
import traceback

sys.dont_write_bytecode = True

from gltf_acceptance_common import (
    SCHEMA, MAX_ALLOCATION, EXTENSIONS, TOLERANCES, accessors, artifact_manifest,
    base_document, checked_size, compare_geometry, cpu_gate, elf_gate,
    empty_output_directory, geometry, pack_rows, require, source_manifest,
    unpack_rows, unit, write_json_new,
)


class GuardedBuffer:
    """Aligned ctypes allocation with canaries around the exact permitted C write range."""
    GUARD = 32

    def __init__(self, size, data=None):
        self.size = checked_size(size)
        require(data is None or len(data) == size, "input allocation length mismatch")
        self.storage = (C.c_ubyte * (size + 2 * self.GUARD))()
        self.base = C.addressof(self.storage)
        self.address = self.base + self.GUARD
        self.pointer = C.c_void_p(self.address)
        self.byte_pointer = C.cast(self.pointer, C.POINTER(C.c_ubyte))
        require(self.address % 4 == 0, "ctypes allocation is not 4-byte aligned")
        C.memset(self.base, 0xA5, self.GUARD)
        C.memset(self.address, 0xCD, size)
        C.memset(self.address + size, 0x5A, self.GUARD)
        if data is not None and size:
            C.memmove(self.address, data, size)
        self.original = data

    def check(self, unchanged=False):
        require(C.string_at(self.base, self.GUARD) == bytes([0xA5]) * self.GUARD,
                "native call overwrote the leading allocation guard")
        require(C.string_at(self.address + self.size, self.GUARD) == bytes([0x5A]) * self.GUARD,
                "native call overwrote the trailing allocation guard")
        if unchanged and self.original is not None:
            require(self.bytes() == self.original, "native call modified a read-only input")

    def bytes(self):
        return C.string_at(self.address, self.size)

    def poison(self):
        C.memset(self.address, 0xE7, self.size)
        self.check()


def bind(lib, name, result, arguments):
    function = getattr(lib, name)  # Missing exported symbols are a hard ABI failure.
    function.restype = result
    function.argtypes = arguments
    return function


def load_bridge(directory, stem, machine):
    path = Path(directory) / f"libbf_intern_{stem}_bridge.so"
    evidence = elf_gate(path, machine)
    # RTLD_NOW detects missing relocations/dependencies immediately, LOCAL avoids global pollution.
    mode = os.RTLD_NOW | os.RTLD_LOCAL
    lib = C.CDLL(str(path.resolve(strict=True)), mode=mode)
    evidence["dlopen"] = "RTLD_NOW|RTLD_LOCAL"
    return lib, evidence


class DracoBridge:
    def __init__(self, lib):
        self.lib = lib
        p, u32, size = C.c_void_p, C.c_uint32, C.c_size_t
        signatures = {
            "encoderCreate": (p, [u32]), "encoderRelease": (None, [p]),
            "encoderSetCompressionLevel": (None, [p, u32]),
            "encoderSetQuantizationBits": (None, [p, u32, u32, u32, u32, u32]),
            "encoderSetIndices": (None, [p, size, u32, p]),
            "encoderSetAttribute": (u32, [p, C.c_char_p, size, C.c_char_p, p, C.c_bool]),
            "encoderEncode": (C.c_bool, [p, C.c_uint8]),
            "encoderGetEncodedVertexCount": (u32, [p]),
            "encoderGetEncodedIndexCount": (u32, [p]),
            "encoderGetByteLength": (C.c_uint64, [p]), "encoderCopy": (None, [p, p]),
            "decoderCreate": (p, []), "decoderRelease": (None, [p]),
            "decoderDecode": (C.c_bool, [p, p, size]),
            "decoderGetVertexCount": (u32, [p]), "decoderGetIndexCount": (u32, [p]),
            "decoderAttributeIsNormalized": (C.c_bool, [p, u32]),
            "decoderReadAttribute": (C.c_bool, [p, u32, size, C.c_char_p]),
            # Actual C headers use size_t for these IDs, unlike the addon ctypes declarations.
            "decoderGetAttributeByteLength": (size, [p, size]),
            "decoderCopyAttribute": (None, [p, size, p]),
            "decoderReadIndices": (C.c_bool, [p, size]),
            "decoderGetIndicesByteLength": (size, [p]), "decoderCopyIndices": (None, [p, p]),
        }
        for name, (result, arguments) in signatures.items():
            bind(lib, name, result, arguments)
        self.symbols = sorted(signatures)
        self.created = {"encoder": 0, "decoder": 0}
        self.released = {"encoder": 0, "decoder": 0}

    @contextmanager
    def handle(self, kind, count=None):
        create, release = getattr(self.lib, kind + "Create"), getattr(self.lib, kind + "Release")
        pointer = create(count) if kind == "encoder" else create()
        require(pointer, f"{kind}Create returned null")
        self.created[kind] += 1
        try:
            yield pointer
        finally:
            # No API call is made with an invalid handle; exactly one matching release per create.
            release(pointer)
            self.released[kind] += 1

    def encode(self, reference, preserve=False, index_type=5123):
        n = len(reference["POSITION"])
        require(0 < n < 65536, "test geometry must fit unsigned short indices")
        inputs = []
        ids = {}
        with self.handle("encoder", n) as encoder:
            for semantic, width in (("POSITION", 3), ("NORMAL", 3), ("TEXCOORD_0", 2)):
                data = pack_rows(reference[semantic])
                source = GuardedBuffer(checked_size(n, width * 4), data)
                inputs.append(source)
                ids[semantic] = int(self.lib.encoderSetAttribute(
                    encoder, semantic.encode(), 5126, f"VEC{width}".encode(), source.pointer, False))
                source.check(unchanged=True)
            # Exercise normalized integer ownership and normalization metadata in addition to geometry.
            color_bytes = bytes([64, 128, 240, 255] * n)
            colors = GuardedBuffer(len(color_bytes), color_bytes)
            ids["COLOR_0"] = int(self.lib.encoderSetAttribute(
                encoder, b"COLOR_0", 5121, b"VEC4", colors.pointer, True))
            inputs.append(colors)
            require(len(set(ids.values())) == len(ids), "Draco returned duplicate attribute IDs")
            code = "H" if index_type == 5123 else "I"
            packed = struct.pack("<" + code * len(reference["indices"]), *reference["indices"])
            indices = GuardedBuffer(len(packed), packed)
            self.lib.encoderSetIndices(encoder, index_type, len(reference["indices"]), indices.pointer)
            inputs.append(indices)
            # C++ setters copy into their own buffers. Poisoning caller storage proves that contract.
            for source in inputs:
                source.check(unchanged=True)
                source.poison()
            self.lib.encoderSetCompressionLevel(encoder, 6)
            self.lib.encoderSetQuantizationBits(encoder, 16, 12, 14, 10, 12)
            require(self.lib.encoderEncode(encoder, int(preserve)), "Draco encoderEncode failed")
            count = int(self.lib.encoderGetEncodedVertexCount(encoder))
            index_count = int(self.lib.encoderGetEncodedIndexCount(encoder))
            require(count == n and index_count == len(reference["indices"]), "encoded Draco counts changed")
            size = checked_size(int(self.lib.encoderGetByteLength(encoder)))
            require(size > 0, "Draco produced an empty encoded buffer")
            output = GuardedBuffer(size)
            self.lib.encoderCopy(encoder, output.pointer)
            output.check()
            encoded = output.bytes()
            again = GuardedBuffer(size)
            self.lib.encoderCopy(encoder, again.pointer)
            again.check()
            require(encoded == again.bytes(), "encoderCopy changed or consumed owned data")
            for source in inputs:
                source.check()
        require(encoded.startswith(b"DRACO"), "Draco bitstream magic is invalid")
        return encoded, ids, count, index_count

    def decode(self, encoded, ids, index_type=5123):
        source = GuardedBuffer(len(encoded), encoded)
        actual = {}
        with self.handle("decoder") as decoder:
            require(self.lib.decoderDecode(decoder, source.pointer, len(encoded)), "Draco decoderDecode failed")
            source.check(unchanged=True)
            n = int(self.lib.decoderGetVertexCount(decoder))
            count = int(self.lib.decoderGetIndexCount(decoder))
            checked_size(n, 12)
            checked_size(count, 4)
            require(n > 0 and count > 0 and count % 3 == 0, "decoded Draco counts are invalid")
            # decoderDecode owns the mesh after returning and must not retain the caller's bitstream.
            source.poison()
            require(not self.lib.decoderReadIndices(decoder, 5126), "invalid index component type was accepted")
            require(self.lib.decoderReadIndices(decoder, index_type), "Draco index conversion failed")
            index_size = 2 if index_type == 5123 else 4
            size = checked_size(count, index_size)
            require(int(self.lib.decoderGetIndicesByteLength(decoder)) == size, "Draco index allocation length mismatch")
            output = GuardedBuffer(size)
            self.lib.decoderCopyIndices(decoder, output.pointer)
            output.check()
            actual["indices"] = list(struct.unpack("<" + ("H" if index_size == 2 else "I") * count, output.bytes()))
            for semantic, width in (("POSITION", 3), ("NORMAL", 3), ("TEXCOORD_0", 2)):
                attribute_id = ids[semantic]
                require(not self.lib.decoderAttributeIsNormalized(decoder, attribute_id), f"float {semantic} incorrectly normalized")
                require(self.lib.decoderReadAttribute(decoder, attribute_id, 5126, f"VEC{width}".encode()),
                        f"Draco attribute conversion failed: {semantic}")
                size = checked_size(n, width * 4)
                require(int(self.lib.decoderGetAttributeByteLength(decoder, attribute_id)) == size,
                        f"Draco {semantic} allocation length mismatch")
                output = GuardedBuffer(size)
                self.lib.decoderCopyAttribute(decoder, attribute_id, output.pointer)
                output.check()
                actual[semantic] = unpack_rows(output.bytes(), width)
            if "COLOR_0" in ids:
                attribute_id = ids["COLOR_0"]
                require(self.lib.decoderAttributeIsNormalized(decoder, attribute_id), "normalized color metadata lost")
                require(self.lib.decoderReadAttribute(decoder, attribute_id, 5121, b"VEC4"), "normalized color conversion failed")
                require(int(self.lib.decoderGetAttributeByteLength(decoder, attribute_id)) == n * 4, "color length mismatch")
                output = GuardedBuffer(n * 4)
                self.lib.decoderCopyAttribute(decoder, attribute_id, output.pointer)
                output.check()
                require(output.bytes() == bytes([64, 128, 240, 255] * n), "normalized colors changed")
            missing = 0xFFFFFFFE
            require(not self.lib.decoderReadAttribute(decoder, missing, 5126, b"VEC3"), "unknown Draco attribute was accepted")
            require(not self.lib.decoderAttributeIsNormalized(decoder, missing), "unknown attribute marked normalized")
            require(int(self.lib.decoderGetAttributeByteLength(decoder, missing)) == 0, "unknown attribute allocated memory")
            untouched = GuardedBuffer(16, bytes([0x3C]) * 16)
            self.lib.decoderCopyAttribute(decoder, missing, untouched.pointer)
            untouched.check(unchanged=True)
        source.check()
        # All arrays are Python-owned here, after decoderRelease.
        return actual

    def reject_corrupt(self, encoded):
        cases = {"empty": b"", "wrong_magic": b"BROKEN" + encoded[6:],
                 "short_header": encoded[:5], "nonsense": bytes(range(32))}
        for name, payload in cases.items():
            source = GuardedBuffer(len(payload), payload)
            with self.handle("decoder") as decoder:
                require(not self.lib.decoderDecode(decoder, source.pointer, len(payload)), f"corrupt Draco {name} accepted")
                # Counts are uninitialized on failure in the real bridge: never read them here.
            source.check(unchanged=True)
        return list(cases)


class MeshoptBridge:
    def __init__(self, lib):
        self.lib = lib
        p, size = C.c_void_p, C.c_size_t
        signatures = {
            "encodeIndexVersion": (None, [C.c_int]), "encodeVertexVersion": (None, [C.c_int]),
            "encodeIndexBufferBound": (size, [size, size]),
            "encodeIndexBuffer": (size, [p, size, p, size]),
            "encodeVertexBufferBound": (size, [size, size]),
            "encodeVertexBuffer": (size, [p, size, p, size, size]),
            "encodeIndexSequenceBound": (size, [size, size]),
            "encodeIndexSequence": (size, [p, size, p, size]),
            "encodeFilterOct": (None, [p, size, size, C.c_int, p]),
            "encodeFilterQuat": (None, [p, size, size, C.c_int, p]),
            "encodeFilterExp": (None, [p, size, size, C.c_int, p, C.c_int]),
        }
        for name in ("decodeVertexBuffer", "decodeIndexBuffer", "decodeIndexSequence"):
            signatures[name] = (C.c_int, [p, size, size, C.POINTER(C.c_ubyte), size])
        for name in ("decodeFilterOct", "decodeFilterQuat", "decodeFilterExp"):
            signatures[name] = (None, [p, size, size])
        for name, (result, arguments) in signatures.items():
            bind(lib, name, result, arguments)
        self.symbols = sorted(signatures)
        lib.encodeIndexVersion(1)
        lib.encodeVertexVersion(0)  # EXT_meshopt_compression, exactly as the real addon.

    def encode(self, data, count, stride, mode):
        require(mode in ("ATTRIBUTES", "TRIANGLES", "INDICES"), "invalid meshopt mode")
        require(len(data) == checked_size(count, stride), "meshopt input byte length mismatch")
        source = GuardedBuffer(len(data), data)
        if mode == "ATTRIBUTES":
            require(stride % 4 == 0 and 0 < stride <= 256, "invalid attribute stride")
            bound = int(self.lib.encodeVertexBufferBound(count, stride))
            encoder = self.lib.encodeVertexBuffer
            arguments = (count, stride)
        else:
            require(stride == 4, "meshopt encoding requires uint32 indices")
            indices = struct.unpack("<" + "I" * count, data)
            vertex_count = max(indices) + 1
            if mode == "TRIANGLES":
                require(count % 3 == 0, "triangle count is not divisible by 3")
                bound = int(self.lib.encodeIndexBufferBound(count, vertex_count))
                encoder = self.lib.encodeIndexBuffer
            else:
                bound = int(self.lib.encodeIndexSequenceBound(count, vertex_count))
                encoder = self.lib.encodeIndexSequence
            arguments = (count,)
        checked_size(bound)
        require(bound > 0, "meshopt returned an empty allocation bound")
        destination = GuardedBuffer(bound)
        written = int(encoder(destination.pointer, bound, source.pointer, *arguments))
        require(0 < written <= bound, f"meshopt encoded length {written} exceeds bound {bound}")
        destination.check()
        source.check(unchanged=True)
        encoded = destination.bytes()[:written]
        # Encoder promises to return zero when the output capacity is insufficient.
        small = GuardedBuffer(1)
        require(int(encoder(small.pointer, 1, source.pointer, *arguments)) == 0,
                "meshopt accepted an insufficient destination capacity")
        small.check()
        source.check(unchanged=True)
        return encoded

    def decode(self, encoded, count, stride, mode, filter_name=None):
        source = GuardedBuffer(len(encoded), encoded)
        destination = GuardedBuffer(checked_size(count, stride))
        decoder = {"ATTRIBUTES": self.lib.decodeVertexBuffer, "TRIANGLES": self.lib.decodeIndexBuffer,
                   "INDICES": self.lib.decodeIndexSequence}[mode]
        status = int(decoder(destination.pointer, count, stride, source.byte_pointer, len(encoded)))
        require(status == 0, f"meshopt {mode} failed with error code {status}")
        source.check(unchanged=True)
        destination.check()
        if filter_name is not None:
            require(mode == "ATTRIBUTES", "filters may only apply to attributes")
            function = {"EXPONENTIAL": self.lib.decodeFilterExp, "OCTAHEDRAL": self.lib.decodeFilterOct,
                        "QUATERNION": self.lib.decodeFilterQuat}[filter_name]
            function(destination.pointer, count, stride)
            destination.check()
        return destination.bytes()

    def filter_encode(self, rows, stride, filter_name, bits):
        count = len(rows)
        data = pack_rows(rows)
        source = GuardedBuffer(len(data), data)
        destination = GuardedBuffer(checked_size(count, stride))
        if filter_name == "EXPONENTIAL":
            require(stride == len(rows[0]) * 4, "exponential filter float layout mismatch")
            self.lib.encodeFilterExp(destination.pointer, count, stride, bits, source.pointer, 1)
        elif filter_name == "OCTAHEDRAL":
            require(stride == 8 and len(rows[0]) == 4, "oct filter needs 4 floats and signed short output")
            self.lib.encodeFilterOct(destination.pointer, count, stride, bits, source.pointer)
        else:
            require(filter_name == "QUATERNION" and stride == 8 and len(rows[0]) == 4, "invalid quaternion layout")
            self.lib.encodeFilterQuat(destination.pointer, count, stride, bits, source.pointer)
        source.check(unchanged=True)
        destination.check()
        return destination.bytes()

    def reject_corrupt(self, encoded, count, stride, mode):
        decoder = {"ATTRIBUTES": self.lib.decodeVertexBuffer, "TRIANGLES": self.lib.decodeIndexBuffer,
                   "INDICES": self.lib.decodeIndexSequence}[mode]
        cases = {"empty": b"", "wrong_header": b"\x00" + encoded[1:], "one_byte": encoded[:1]}
        statuses = {}
        for name, payload in cases.items():
            source = GuardedBuffer(len(payload), payload)
            destination = GuardedBuffer(checked_size(count, stride))
            status = int(decoder(destination.pointer, count, stride, source.byte_pointer, len(payload)))
            require(status != 0, f"corrupt meshopt {mode}/{name} accepted")
            source.check(unchanged=True)
            destination.check()  # Decoders may partially write output even when they return an error.
            statuses[name] = status
        return statuses

    def roundtrip(self, reference):
        streams = []
        actual = {}
        errors = {}
        count = len(reference["POSITION"])
        for semantic, stride in (("POSITION", 12), ("NORMAL", 12), ("TEXCOORD_0", 8)):
            filter_name = "EXPONENTIAL" if semantic in ("POSITION", "NORMAL") else None
            data = (self.filter_encode(reference[semantic], stride, filter_name, 12)
                    if filter_name else pack_rows(reference[semantic]))
            encoded = self.encode(data, count, stride, "ATTRIBUTES")
            decoded = self.decode(encoded, count, stride, "ATTRIBUTES", filter_name)
            actual[semantic] = unpack_rows(decoded, stride // 4)
            if filter_name is None:
                require(decoded == data, "unfiltered meshopt attribute bytes changed")
            streams.append({"semantic": semantic, "encoded": encoded, "count": count, "stride": stride,
                            "mode": "ATTRIBUTES", "filter": filter_name})
            errors[semantic] = self.reject_corrupt(encoded, count, stride, "ATTRIBUTES")
        indices = reference["indices"]
        data = struct.pack("<" + "I" * len(indices), *indices)
        encoded = self.encode(data, len(indices), 4, "TRIANGLES")
        decoded = self.decode(encoded, len(indices), 2, "TRIANGLES")
        actual["indices"] = list(struct.unpack("<" + "H" * len(indices), decoded))
        # Index buffer encoding may rotate triangle vertices; compare winding/topology separately.
        decoded32 = self.decode(encoded, len(indices), 4, "TRIANGLES")
        require(list(struct.unpack("<" + "I" * len(indices), decoded32)) == actual["indices"],
                "meshopt 16-bit and 32-bit index conversions disagree")
        streams.append({"semantic": "indices", "encoded": encoded, "count": len(indices), "stride": 2,
                        "mode": "TRIANGLES", "filter": None})
        errors["indices"] = self.reject_corrupt(encoded, len(indices), 2, "TRIANGLES")
        # Nontriangle sequence API has a separate codec and must preserve exact order and duplicates.
        sequence = [7, 0, 7, 3, 1, 5, 2, 2, 6]
        sequence_data = struct.pack("<" + "I" * len(sequence), *sequence)
        sequence_encoded = self.encode(sequence_data, len(sequence), 4, "INDICES")
        for stride, code in ((2, "H"), (4, "I")):
            restored = self.decode(sequence_encoded, len(sequence), stride, "INDICES")
            require(list(struct.unpack("<" + code * len(sequence), restored)) == sequence, "index sequence changed")
        errors["sequence"] = self.reject_corrupt(sequence_encoded, len(sequence), 4, "INDICES")
        filter_results = {}
        for name, rows, bits in (
            ("OCTAHEDRAL", [tuple(n) + (1.0,) for n in reference["NORMAL"]], 12),
            ("QUATERNION", [unit((0.13, -0.25, 0.31, 0.88)), unit((0.5, 0.5, 0.5, 0.5))], 12),
        ):
            filtered = self.filter_encode(rows, 8, name, bits)
            encoded_filter = self.encode(filtered, len(rows), 8, "ATTRIBUTES")
            restored = self.decode(encoded_filter, len(rows), 8, "ATTRIBUTES", name)
            values = struct.unpack("<" + "h" * (len(rows) * 4), restored)
            maximum = 0.0
            for i, expected in enumerate(rows):
                got = [x / 32767.0 for x in values[i * 4:i * 4 + 4]]
                # q and -q represent the same rotation.
                error = max(abs(a - b) for a, b in zip(got, expected))
                if name == "QUATERNION":
                    error = min(error, max(abs(a + b) for a, b in zip(got, expected)))
                require(error <= 0.004, f"{name} filter exceeded tolerance: {error}")
                maximum = max(maximum, error)
            filter_results[name] = {"maximum_absolute_error": maximum, "tolerance": 0.004}
        return actual, streams, errors, filter_results


def write_binary_new(path, data):
    with Path(path).open("xb") as stream:
        stream.write(data)


def make_fixtures(directory, draco_result, streams, source, libraries):
    encoded, ids, vertex_count, index_count = draco_result
    draco = base_document(EXTENSIONS["draco"])
    draco["accessors"] = accessors(vertex_count, index_count)
    draco["buffers"] = [{"uri": "draco.bin", "byteLength": len(encoded)}]
    draco["bufferViews"] = [{"buffer": 0, "byteOffset": 0, "byteLength": len(encoded)}]
    draco["meshes"][0]["primitives"][0]["extensions"] = {
        EXTENSIONS["draco"]: {"bufferView": 0, "attributes": {key: ids[key] for key in ("POSITION", "NORMAL", "TEXCOORD_0")}}}
    write_binary_new(directory / "draco.bin", encoded)
    write_json_new(directory / "draco.gltf", draco)
    bad_draco = b"BROKEN" + encoded[6:]
    write_binary_new(directory / "draco-corrupt.bin", bad_draco)
    corrupt = copy.deepcopy(draco)
    corrupt["buffers"][0]["uri"] = "draco-corrupt.bin"
    write_json_new(directory / "draco-corrupt.gltf", corrupt)

    meshopt = base_document(EXTENSIONS["meshopt"])
    meshopt["accessors"] = accessors(vertex_count, index_count)
    compressed = bytearray()
    fallback_offset = 0
    views = []
    for i, item in enumerate(streams):
        while len(compressed) % 4:
            compressed.append(0)
        offset = len(compressed)
        compressed.extend(item["encoded"])
        size = checked_size(item["count"], item["stride"])
        extension = {"buffer": 0, "byteOffset": offset, "byteLength": len(item["encoded"]),
                     "byteStride": item["stride"], "count": item["count"], "mode": item["mode"]}
        if item["filter"]:
            extension["filter"] = item["filter"]
        view = {"buffer": 1, "byteOffset": fallback_offset, "byteLength": size,
                "target": 34962 if item["mode"] == "ATTRIBUTES" else 34963,
                "extensions": {EXTENSIONS["meshopt"]: extension}}
        if item["mode"] == "ATTRIBUTES":
            view["byteStride"] = item["stride"]
        views.append(view)
        meshopt["accessors"][i]["bufferView"] = i
        fallback_offset += (size + 3) & ~3
    meshopt["bufferViews"] = views
    # Required compression permits an omitted fallback buffer. Its extension prevents load attempts.
    meshopt["buffers"] = [{"uri": "meshopt.bin", "byteLength": len(compressed)},
                           {"byteLength": fallback_offset,
                            "extensions": {EXTENSIONS["meshopt"]: {"fallback": True}}}]
    write_binary_new(directory / "meshopt.bin", compressed)
    write_json_new(directory / "meshopt.gltf", meshopt)
    corrupt = copy.deepcopy(meshopt)
    corrupt["buffers"][0]["uri"] = "meshopt-corrupt.bin"
    bad_meshopt = bytearray(compressed)
    # Position stream header corruption cannot decode into fallback geometry unnoticed.
    bad_meshopt[views[0]["extensions"][EXTENSIONS["meshopt"]]["byteOffset"]] = 0
    write_binary_new(directory / "meshopt-corrupt.bin", bad_meshopt)
    write_json_new(directory / "meshopt-corrupt.gltf", corrupt)
    unsupported = copy.deepcopy(draco)
    unsupported["extensionsUsed"].append("OHOS_acceptance_unsupported_extension")
    unsupported["extensionsRequired"].append("OHOS_acceptance_unsupported_extension")
    write_json_new(directory / "unsupported-required.gltf", unsupported)
    with (directory / "invalid-json.gltf").open("x", encoding="utf-8") as stream:
        stream.write('{"asset": {"version": "2.0"}, invalid JSON\n')
    write_json_new(directory / "source-manifest.json", source)
    manifest = {"schema": SCHEMA, "geometry": geometry(), "tolerances": TOLERANCES,
                "libraries": libraries, "files": artifact_manifest(directory),
                "fixture_generation": "actual bf_intern bridge C ABI; no canned or fallback geometry",
                "positive": {"draco": "draco.gltf", "meshopt": "meshopt.gltf"},
                "negative": {"draco": "draco-corrupt.gltf", "meshopt": "meshopt-corrupt.gltf",
                             "unsupported_required": "unsupported-required.gltf", "invalid_json": "invalid-json.gltf"}}
    write_json_new(directory / "fixture-manifest.json", manifest)
    return manifest


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lib-dir", type=Path, required=True, help="directory containing both actual Blender bridge .so files")
    parser.add_argument("--fixtures", type=Path, required=True, help="new or empty generated-fixture directory")
    parser.add_argument("--report", type=Path, required=True, help="new JSON evidence file; parent directory may exist")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[4], help="Blender checkout root")
    parser.add_argument("--expect-machine", choices=("aarch64",), default="aarch64")
    parser.add_argument("--iterations", type=int, default=4, help="bounded repeated create/decode/release cycles (1..64)")
    return parser.parse_args()


def run(args, report):
    report["runtime"] = cpu_gate(args.expect_machine)
    require(C.sizeof(C.c_size_t) == 8 and C.sizeof(C.c_uint32) == 4 and C.sizeof(C.c_bool) == 1,
            "ctypes ABI scalar widths differ from the bridge headers")
    require(1 <= args.iterations <= 64, "iterations must be in range 1..64")
    report["source"] = source_manifest(args.source_root)
    output = empty_output_directory(args.fixtures)
    require(not args.report.exists(), f"report already exists: {args.report}")
    draco_lib, draco_evidence = load_bridge(args.lib_dir, "draco", args.expect_machine)
    meshopt_lib, meshopt_evidence = load_bridge(args.lib_dir, "meshopt", args.expect_machine)
    report["libraries"] = {"draco": draco_evidence, "meshopt": meshopt_evidence}
    draco, meshopt = DracoBridge(draco_lib), MeshoptBridge(meshopt_lib)
    report["exported_symbols"] = {"draco": draco.symbols, "meshopt": meshopt.symbols}
    # Allocation guards are rejected in Python before any unsafe native call can occur.
    for count, stride in ((-1, 4), (MAX_ALLOCATION + 1, 1), (8, 0), (MAX_ALLOCATION, 4)):
        try:
            checked_size(count, stride)
        except AssertionError:
            continue
        raise AssertionError(f"unsafe allocation accepted: count={count}, stride={stride}")
    report["allocation_policy"] = {"maximum_bytes": MAX_ALLOCATION, "guards_each_side": 32,
                                   "invalid_sizes_rejected_before_C": True}
    reference = geometry()
    report["draco"] = {"cycles": []}
    fixture_draco = None
    for i in range(args.iterations):
        index_type = 5123 if i % 2 == 0 else 5125
        encoded_result = draco.encode(reference, preserve=bool(i % 2), index_type=index_type)
        encoded, ids, _, _ = encoded_result
        actual = draco.decode(encoded, ids, index_type)
        evidence = compare_geometry(actual, reference, TOLERANCES["draco"])
        evidence.update({"index_component_type": index_type, "preserve_triangle_order": bool(i % 2),
                         "encoded_bytes": len(encoded), "corrupt_cases": draco.reject_corrupt(encoded)})
        report["draco"]["cycles"].append(evidence)
        if fixture_draco is None:
            fixture_draco = encoded_result
    require(draco.created == draco.released, "Draco handle ownership mismatch")
    report["draco"]["handles_created"] = draco.created
    report["draco"]["handles_released"] = draco.released
    report["draco"]["caller_inputs_poisoned_after_setters"] = True
    report["meshopt"] = {"cycles": []}
    fixture_streams = None
    for _ in range(args.iterations):
        actual, streams, corrupt, filters = meshopt.roundtrip(reference)
        evidence = compare_geometry(actual, reference, TOLERANCES["meshopt"])
        evidence.update({"corrupt_status_codes": corrupt, "filters": filters})
        report["meshopt"]["cycles"].append(evidence)
        if fixture_streams is None:
            fixture_streams = streams
    report["fixtures"] = {"directory": str(output), "manifest": make_fixtures(
        output, fixture_draco, fixture_streams, report["source"], report["libraries"])}


def main():
    args = parse_args()
    report = {"schema": SCHEMA, "stage": "native-bridge-abi", "status": "FAIL", "result": "FAIL",
              "bpy_acceptance": "NOTRUN", "hap_acceptance": "NOTRUN"}
    exit_code = 1
    try:
        run(args, report)
        report["status"] = report["result"] = "PASS"
        exit_code = 0
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
    try:
        write_json_new(args.report, report)
    except Exception:
        traceback.print_exc()
        return 1
    print(json.dumps({"stage": report["stage"], "status": report["status"], "report": str(args.report)}, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
