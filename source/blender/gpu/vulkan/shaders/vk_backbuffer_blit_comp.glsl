/* SPDX-FileCopyrightText: 2025 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "vk_backbuffer_blit_infos.hh"

COMPUTE_SHADER_CREATE_INFO(vk_backbuffer_blit)

float srgb_to_linearrgb(float c)
{
  if (c < 0.04045f) {
    return (c < 0.0f) ? 0.0f : c * (1.0f / 12.92f);
  }

  return pow((c + 0.055f) * (1.0f / 1.055f), 2.4f);
}

float3 nonlinear_to_linear_scrgb(float3 c)
{
#ifdef USE_GAMMA22
  return pow(c, float3(2.2f));
#else
  return float3(srgb_to_linearrgb(c.r), srgb_to_linearrgb(c.g), srgb_to_linearrgb(c.b));
#endif
}

void main()
{
  int2 dst_texel = int2(gl_GlobalInvocationID.xy);
  int2 dst_size = int2(imageSize(dst_img));
  if (any(greaterThanEqual(dst_texel, dst_size))) {
    return;
  }
  int2 src_size = int2(imageSize(src_img));
  float4 color;
  if (all(equal(src_size, dst_size))) {
    color = imageLoad(src_img, int2(dst_texel.x, src_size.y - dst_texel.y - 1));
  }
  else {
    float2 uv = (float2(dst_texel) + 0.5f) / float2(dst_size);
    float2 src_pos = float2(uv.x, 1.0f - uv.y) * float2(src_size) - 0.5f;
    int2 lo = int2(floor(src_pos));
    int2 hi = lo + int2(1);
    float2 weight = fract(src_pos);
    lo = clamp(lo, int2(0), src_size - int2(1));
    hi = clamp(hi, int2(0), src_size - int2(1));
    color = mix(mix(imageLoad(src_img, lo), imageLoad(src_img, int2(hi.x, lo.y)), weight.x),
                mix(imageLoad(src_img, int2(lo.x, hi.y)), imageLoad(src_img, hi), weight.x),
                weight.y);
  }
  /*
   * Convert from extended sRGB non-linear to linear.
   *
   * Preserves negative wide gamut values with sign/abs. May use either gamma 2.2
   * decode to match most SDR sRGB displays, or the piecewise sRGB function to
   * match Windows SDR applications in HDR node.
   */
  color.rgb = sign(color.rgb) * nonlinear_to_linear_scrgb(abs(color.rgb)) * sdr_scale;
  imageStore(dst_img, int2(dst_texel), color);
}
