#!/usr/bin/env python3
"""Generate guarded HarmonyOS Blender icon layers with native drawing and stdlib.

Run with python3; all four destination PNGs and the receipt must be absent.
Use --workroot with a fresh, pre-created directory layout to reproduce outputs.
No clipping, launcher mask, background shape, or geometry approximation is used.
"""

import argparse
from collections import Counter
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import sys
import xml.etree.ElementTree as ET
import zlib


SIZE = 1024
SAFE_WIDTH = 576
SAFE_OFFSET = (SIZE - SAFE_WIDTH) / 2
# Native AA at the leftmost SVG curve needs a small unmasked inset.
ARTWORK_WIDTH = SAFE_WIDTH - 4
OFFSET = (SIZE - ARTWORK_WIDTH) / 2
SCALE = ARTWORK_WIDTH / 128
SOURCE_SHA256 = "fd18ae29afd0743a6eee912fdfab71476f6da0f6699a16c1ecd7db7ea8a5de59"
SOURCE_DEFAULT = Path(__file__).resolve().parents[3] / "release/freedesktop/icons/scalable/apps/blender.svg"
WORKROOT_DEFAULT = None
MEDIA_DIRS = (
    "host/deveco/AppScope/resources/base/media",
    "host/deveco/entry/src/main/resources/base/media",
)
FILLS = ("#ffffff", "#265787", "#e87d0d")
BACKGROUND_RGBA = (32, 33, 36, 255)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def absent(path):
    require(not os.path.lexists(path), f"Refusing to overwrite existing target: {path}")


def bind(lib, name, restype, *argtypes):
    fn = getattr(lib, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


class BitmapFormat(C.Structure):
    # SDK drawing_bitmap.h: two enums in colorFormat, alphaFormat order.
    _fields_ = [("colorFormat", C.c_int), ("alphaFormat", C.c_int)]


class NativeDrawing:
    def __init__(self):
        self.lib = C.CDLL("libnative_drawing.so")
        p, u, i, f, b = C.c_void_p, C.c_uint32, C.c_int, C.c_float, C.c_bool
        bindings = (
            ("BitmapCreate", p, ()),
            ("BitmapDestroy", None, (p,)),
            ("BitmapBuild", None, (p, u, u, C.POINTER(BitmapFormat))),
            ("BitmapGetWidth", u, (p,)),
            ("BitmapGetHeight", u, (p,)),
            ("BitmapGetColorFormat", i, (p,)),
            ("BitmapGetAlphaFormat", i, (p,)),
            ("BitmapGetRowBytes", i, (p, C.POINTER(u))),
            ("BitmapGetPixels", p, (p,)),
            ("CanvasCreate", p, ()),
            ("CanvasDestroy", None, (p,)),
            ("CanvasBind", None, (p, p)),
            ("CanvasClear", None, (p, u)),
            ("CanvasTranslate", None, (p, f, f)),
            ("CanvasScale", None, (p, f, f)),
            ("CanvasAttachBrush", None, (p, p)),
            ("CanvasDetachBrush", None, (p,)),
            ("CanvasDrawPath", None, (p, p)),
            ("BrushCreate", p, ()),
            ("BrushDestroy", None, (p,)),
            ("BrushSetAntiAlias", None, (p, b)),
            ("BrushSetColor", None, (p, u)),
            ("PathCreate", p, ()),
            ("PathDestroy", None, (p,)),
            ("PathBuildFromSvgString", b, (p, C.c_char_p)),
            ("PathSetFillType", None, (p, i)),
            ("PathGetBounds", None, (p, p)),
            ("RectCreate", p, (f, f, f, f)),
            ("RectDestroy", None, (p,)),
            ("RectGetLeft", f, (p,)),
            ("RectGetTop", f, (p,)),
            ("RectGetRight", f, (p,)),
            ("RectGetBottom", f, (p,)),
        )
        for suffix, result, arguments in bindings:
            setattr(self, suffix, bind(self.lib, "OH_Drawing_" + suffix, result, *arguments))

    def rasterize(self, paths=None):
        bitmap = canvas = brush = None
        path_bounds = []
        try:
            bitmap = self.BitmapCreate()
            require(bitmap, "BitmapCreate returned null")
            # SDK drawing_types.h: RGBA_8888 = 4, PREMUL = 2.
            fmt = BitmapFormat(4, 2)
            self.BitmapBuild(bitmap, SIZE, SIZE, C.byref(fmt))
            require(self.BitmapGetWidth(bitmap) == SIZE and self.BitmapGetHeight(bitmap) == SIZE,
                    "Native bitmap dimensions differ")
            require(self.BitmapGetColorFormat(bitmap) == 4 and self.BitmapGetAlphaFormat(bitmap) == 2,
                    "Native bitmap formats differ")
            canvas = self.CanvasCreate()
            require(canvas, "CanvasCreate returned null")
            self.CanvasBind(canvas, bitmap)
            self.CanvasClear(canvas, 0 if paths is not None else 0xFF202124)
            if paths is not None:
                self.CanvasTranslate(canvas, OFFSET, OFFSET)
                self.CanvasScale(canvas, SCALE, SCALE)
                brush = self.BrushCreate()
                require(brush, "BrushCreate returned null")
                self.BrushSetAntiAlias(brush, True)
                for node in paths:
                    path = rect = None
                    try:
                        path = self.PathCreate()
                        require(path, "PathCreate returned null")
                        require(self.PathBuildFromSvgString(path, node.attrib["d"].encode("ascii")),
                                "Native SVG path parsing failed")
                        # SVG default fill-rule is nonzero; preserve source path order.
                        self.PathSetFillType(path, 0)
                        rect = self.RectCreate(0, 0, 0, 0)
                        require(rect, "RectCreate returned null")
                        self.PathGetBounds(path, rect)
                        path_bounds.append({
                            "fill": node.attrib["fill"],
                            "svg_d_sha256": sha256(node.attrib["d"].encode("ascii")),
                            "native_path_bounds": [self.RectGetLeft(rect), self.RectGetTop(rect),
                                                   self.RectGetRight(rect), self.RectGetBottom(rect)],
                        })
                        self.BrushSetColor(brush, 0xFF000000 | int(node.attrib["fill"][1:], 16))
                        # Attach after each color change: the canvas copies brush state.
                        self.CanvasAttachBrush(canvas, brush)
                        self.CanvasDrawPath(canvas, path)
                        self.CanvasDetachBrush(canvas)
                    finally:
                        if rect:
                            self.RectDestroy(rect)
                        if path:
                            self.PathDestroy(path)
            row_bytes = C.c_uint32()
            require(self.BitmapGetRowBytes(bitmap, C.byref(row_bytes)) == 0,
                    "BitmapGetRowBytes returned an error")
            require(row_bytes.value >= SIZE * 4, "Native stride is too short")
            pixels = self.BitmapGetPixels(bitmap)
            require(pixels, "BitmapGetPixels returned null")
            native_bytes = C.string_at(pixels, row_bytes.value * SIZE)
            packed = b"".join(native_bytes[y * row_bytes.value:y * row_bytes.value + SIZE * 4]
                              for y in range(SIZE))
            return packed, {"width": SIZE, "height": SIZE, "color_format": 4,
                            "alpha_format": 2, "row_bytes": row_bytes.value,
                            "paths": path_bounds}
        finally:
            if canvas:
                self.CanvasDetachBrush(canvas)
                self.CanvasDestroy(canvas)
            if brush:
                self.BrushDestroy(brush)
            if bitmap:
                self.BitmapDestroy(bitmap)


def unpremultiply(raw):
    require(len(raw) == SIZE * SIZE * 4, "Native pixel buffer size differs")
    result = bytearray(len(raw))
    maximum_roundtrip_error = 0
    for start in range(0, len(raw), 4):
        alpha = raw[start + 3]
        require(all(channel <= alpha for channel in raw[start:start + 3]),
                "Native RGB channels exceed premultiplied alpha")
        if alpha:
            result[start + 3] = alpha
            for channel in range(3):
                value = min(255, (raw[start + channel] * 255 + alpha // 2) // alpha)
                result[start + channel] = value
                reconstructed = (value * alpha + 127) // 255
                maximum_roundtrip_error = max(maximum_roundtrip_error,
                                              abs(reconstructed - raw[start + channel]))
        else:
            require(raw[start:start + 3] == b"\x00\x00\x00", "Transparent premul pixel is nonzero")
    require(maximum_roundtrip_error <= 1, "Unpremultiplied edge roundtrip differs by more than one byte")
    return bytes(result), maximum_roundtrip_error


def pixel(rgba, x, y):
    start = (y * SIZE + x) * 4
    return list(rgba[start:start + 4])


def foreground_validation(rgba, premul):
    alphas = Counter()
    opaque_colors = Counter()
    xmin = ymin = SIZE
    xmax = ymax = -1
    edge_examples = []
    for index in range(SIZE * SIZE):
        start = index * 4
        alpha = rgba[start + 3]
        alphas[alpha] += 1
        if alpha:
            x, y = index % SIZE, index // SIZE
            xmin, ymin = min(xmin, x), min(ymin, y)
            xmax, ymax = max(xmax, x), max(ymax, y)
            require(SAFE_OFFSET <= x < SAFE_OFFSET + SAFE_WIDTH and SAFE_OFFSET <= y < SAFE_OFFSET + SAFE_WIDTH,
                    f"Nontransparent pixel escapes central {SAFE_WIDTH}px region at {x},{y}")
            if alpha == 255:
                opaque_colors[tuple(rgba[start:start + 3])] += 1
            elif 96 <= alpha <= 224 and len(edge_examples) < 8:
                edge_examples.append({"x": x, "y": y,
                                      "premultiplied_rgba": list(premul[start:start + 4]),
                                      "straight_rgba": list(rgba[start:start + 4])})
        else:
            require(rgba[start:start + 3] == b"\x00\x00\x00", "Transparent PNG pixel is nonzero")
    transparent = alphas[0]
    opaque = alphas[255]
    partial = SIZE * SIZE - transparent - opaque
    require(transparent > 0 and opaque > 0 and partial > 0, "Foreground lacks transparent/opaque/AA pixels")
    for fill in FILLS:
        expected = tuple(bytes.fromhex(fill[1:]))
        require(opaque_colors[expected] > 1000, f"Missing official opaque fill: {fill}")
    sample_positions = {
        "top_left": (0, 0), "top_right": (1023, 0),
        "bottom_left": (0, 1023), "bottom_right": (1023, 1023),
        "outside_left_safe_region": (223, 512), "outside_right_safe_region": (800, 512),
        "blue_center": (602, 548), "white_ring": (602, 453), "orange_body": (602, 710),
    }
    samples = {name: {"x": x, "y": y, "rgba": pixel(rgba, x, y)}
               for name, (x, y) in sample_positions.items()}
    require(samples["blue_center"]["rgba"] == [38, 87, 135, 255], "Blue sample differs")
    require(samples["white_ring"]["rgba"] == [255, 255, 255, 255], "White sample differs")
    require(samples["orange_body"]["rgba"] == [232, 125, 13, 255], "Orange sample differs")
    for name in ("top_left", "top_right", "bottom_left", "bottom_right",
                 "outside_left_safe_region", "outside_right_safe_region"):
        require(samples[name]["rgba"] == [0, 0, 0, 0], f"Transparent sample differs: {name}")
    return {
        "nontransparent_bbox_inclusive": [xmin, ymin, xmax, ymax],
        "nontransparent_bbox_size": [xmax - xmin + 1, ymax - ymin + 1],
        "transparent_margins_px": {"left": xmin, "top": ymin,
                                    "right": SIZE - 1 - xmax, "bottom": SIZE - 1 - ymax},
        "transparent_pixels": transparent, "opaque_pixels": opaque, "partial_alpha_pixels": partial,
        "nontransparent_pixels": opaque + partial,
        "all_nontransparent_pixels_inside_central_576px_square": True,
        "all_fully_transparent_pixels_zero_rgba": True,
        "alpha_levels_present": sorted(alphas),
        "opaque_official_color_counts": {
            fill: opaque_colors[tuple(bytes.fromhex(fill[1:]))] for fill in FILLS},
        "sample_pixels": samples,
        "antialiased_edge_examples": edge_examples,
    }


def background_validation(rgba):
    expected = bytes(BACKGROUND_RGBA) * (SIZE * SIZE)
    require(rgba == expected, "Background contains pixels other than opaque #202124 RGBA")
    return {"all_pixels_rgba": list(BACKGROUND_RGBA), "opaque_pixels": SIZE * SIZE,
            "transparent_pixels": 0, "partial_alpha_pixels": 0,
            "sample_pixels": [{"x": x, "y": y, "rgba": pixel(rgba, x, y)}
                              for x, y in ((0, 0), (512, 512), (1023, 1023))]}


def png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_png(rgba):
    # PNG stores straight RGBA, 8-bit channels, noninterlaced, with filter 0.
    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    scanlines = b"".join(b"\x00" + rgba[y * SIZE * 4:(y + 1) * SIZE * 4] for y in range(SIZE))
    return PNG_SIGNATURE + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", zlib.compress(scanlines, 9)) + png_chunk(b"IEND", b"")


def validate_png(blob, expected_pixels):
    require(blob[:8] == PNG_SIGNATURE, "PNG signature differs")
    position, chunks, compressed = 8, [], bytearray()
    header = None
    while position < len(blob):
        require(position + 12 <= len(blob), "Truncated PNG chunk")
        length = struct.unpack(">I", blob[position:position + 4])[0]
        kind = blob[position + 4:position + 8]
        data = blob[position + 8:position + 8 + length]
        require(len(data) == length and position + 12 + length <= len(blob), "Truncated PNG data")
        crc = struct.unpack(">I", blob[position + 8 + length:position + 12 + length])[0]
        require(crc == zlib.crc32(kind + data) & 0xFFFFFFFF, "PNG chunk CRC differs")
        chunks.append(kind.decode("ascii"))
        if kind == b"IHDR":
            require(header is None, "Duplicate PNG IHDR")
            header = struct.unpack(">IIBBBBB", data)
        elif kind == b"IDAT":
            compressed.extend(data)
        elif kind == b"IEND":
            require(data == b"", "IEND payload is nonempty")
        position += length + 12
    require(chunks == ["IHDR", "IDAT", "IEND"], "PNG chunk layout differs")
    require(header == (1024, 1024, 8, 6, 0, 0, 0), "PNG IHDR differs from 1024x1024 8-bit RGBA")
    raw = zlib.decompress(compressed)
    require(len(raw) == SIZE * (1 + SIZE * 4), "PNG decompressed length differs")
    decoded = bytearray()
    for y in range(SIZE):
        start = y * (1 + SIZE * 4)
        require(raw[start] == 0, "PNG row filter differs")
        decoded.extend(raw[start + 1:start + 1 + SIZE * 4])
    require(bytes(decoded) == expected_pixels, "PNG decoded RGBA differs from validated raster")
    return {"signature_valid": True, "ihdr": {"width": 1024, "height": 1024,
            "bit_depth": 8, "color_type": 6, "color_type_name": "RGBA",
            "compression_method": 0, "filter_method": 0, "interlace_method": 0},
            "chunks": chunks, "all_chunk_crcs_valid": True, "decoded_pixels_match": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workroot", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    args = parser.parse_args()
    root, source = args.workroot.resolve(strict=True), args.source.resolve(strict=True)
    receipt_path = root / "state/blender-layered-icon-generation-12.json"
    require(receipt_path.parent.is_dir(), "Receipt state directory must exist")
    targets = []
    for rel in MEDIA_DIRS:
        directory = root / rel
        require(directory.is_dir(), f"Resource media directory must exist: {directory}")
        require(directory.resolve(strict=True).is_relative_to(root), "Resource directory escapes workroot")
        targets.extend((directory / "app_icon_foreground.png", directory / "app_icon_background.png"))
    for path in [*targets, receipt_path]:
        absent(path)
    source_bytes = source.read_bytes()
    require(sha256(source_bytes) == SOURCE_SHA256, "Official source SHA-256 differs")
    svg = ET.fromstring(source_bytes)
    require(svg.tag == "{http://www.w3.org/2000/svg}svg", "Source is not SVG")
    require(svg.attrib == {"viewBox": "0 0 128 128"}, "Unexpected root geometry attributes")
    paths = list(svg)
    require(len(paths) == 3, "Source must have exactly three filled paths")
    require(tuple(node.attrib.get("fill") for node in paths) == FILLS, "Source fill order differs")
    for node in paths:
        require(node.tag == "{http://www.w3.org/2000/svg}path" and set(node.attrib) == {"fill", "d"},
                "Unexpected source path geometry attributes")
    native = NativeDrawing()
    foreground_premul, foreground_native = native.rasterize(paths)
    foreground, roundtrip_error = unpremultiply(foreground_premul)
    fg_check = foreground_validation(foreground, foreground_premul)
    background_premul, background_native = native.rasterize()
    background, background_roundtrip_error = unpremultiply(background_premul)
    bg_check = background_validation(background)
    layers = {}
    for name, rgba, native_info, check, error in (
        ("foreground", foreground, foreground_native, fg_check, roundtrip_error),
        ("background", background, background_native, bg_check, background_roundtrip_error),
    ):
        png = encode_png(rgba)
        layers[name] = {"blob": png, "pixels": rgba, "sha256": sha256(png), "bytes": len(png),
                        "pixel_rgba_sha256": sha256(rgba), "native_bitmap": native_info,
                        "premultiply_roundtrip_max_channel_error": error,
                        "png_validation": validate_png(png, rgba), "pixel_validation": check}
    # Recheck every destination after all rendering and validation, then use O_EXCL
    # via mode xb for each file. Existing icons are never replaced or removed.
    for path in [*targets, receipt_path]:
        absent(path)
    outputs = []
    for path in targets:
        name = "foreground" if path.name == "app_icon_foreground.png" else "background"
        layer = layers[name]
        with path.open("xb") as handle:
            handle.write(layer["blob"])
            handle.flush()
            os.fsync(handle.fileno())
        written = path.read_bytes()
        require(written == layer["blob"], f"Written PNG differs: {path}")
        validate_png(written, layer["pixels"])
        outputs.append({"path": str(path), "layer": name, "bytes": len(written),
                        "sha256": sha256(written), "readback_verified": True})
    receipt = {
        "schema": 1, "generation": 12, "generated_utc": datetime.now(timezone.utc).isoformat(),
        "generator": str(Path(__file__).resolve()), "generator_sha256": sha256(Path(__file__).read_bytes()),
        "runtime": {"platform": platform.platform(), "python": sys.version,
                    "native_library": "libnative_drawing.so", "image_dependencies": "none; Python stdlib only"},
        "source": {"path": str(source), "sha256": SOURCE_SHA256,
                   "viewBox": [0, 0, 128, 128], "filled_paths": 3, "fills_in_source_order": list(FILLS)},
        "placement": {"canvas_size": [SIZE, SIZE], "safe_region_width_px": SAFE_WIDTH,
                      "safe_region_bounds_exclusive": [224, 224, 800, 800],
                      "viewbox_render_width_px": ARTWORK_WIDTH,
                      "antialiasing_inset_px_per_side": 2,
                      "uniform_scale": SCALE, "translation_px": [OFFSET, OFFSET],
                      "native_calls_in_order": [f"CanvasTranslate({OFFSET:g}, {OFFSET:g})",
                                                f"CanvasScale({SCALE:g}, {SCALE:g})"],
                      "svg_fill_rule": "nonzero", "antialiasing": True,
                      "mask_applied": False, "preclipping_applied": False, "rounded_corners_applied": False},
        "alpha_conversion": {"native": "RGBA_8888 premultiplied", "png": "RGBA straight alpha",
                             "formula": "alpha=0 => RGBA=0; channel=min(255,(premul*255+alpha//2)//alpha)"},
        "layers": {name: {key: value for key, value in layer.items() if key not in ("blob", "pixels")}
                   for name, layer in layers.items()},
        "outputs": outputs, "overwrite_policy": "all targets absent before rendering and writing; exclusive xb writes",
        "verification_scope": "Native CPU SVG rasterization, PNG structure/CRC/stdlib decoding, exact pixel checks and file readback; no desktop visual proof or HarmonyOS runtime decoder test",
    }
    absent(receipt_path)
    with receipt_path.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    require(json.loads(receipt_path.read_text(encoding="utf-8")) == receipt, "Receipt readback differs")
    print(json.dumps({"receipt": str(receipt_path), "outputs": outputs,
                      "foreground_validation": fg_check, "background_validation": bg_check,
                      "premultiply_roundtrip_max_channel_error": roundtrip_error}, indent=2))


if __name__ == "__main__":
    main()
