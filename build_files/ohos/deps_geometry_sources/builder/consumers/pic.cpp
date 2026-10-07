// SPDX-License-Identifier: GPL-2.0-or-later
#include <gmpxx.h>
#include <manifold/manifold.h>
#include <opensubdiv/osd/glslPatchShaderSource.h>
#include <oneapi/tbb/parallel_reduce.h>
#include <oneapi/tbb/blocked_range.h>
extern "C" int geometry_pic_real_apis()
{
  const mpq_class q(7, 3);
  const auto cube = manifold::Manifold::Cube({1, 1, 1});
  const auto basis = OpenSubdiv::Osd::GLSLPatchShaderSource::GetPatchBasisShaderSource();
  const int total = oneapi::tbb::parallel_reduce(oneapi::tbb::blocked_range<int>(0, 10), 0,
      [](const auto &range, int value) { for (int i=range.begin(); i<range.end(); ++i) value += i; return value; },
      [](int a, int b) { return a+b; });
  return total + int(cube.Volume()) + int(q.get_num().get_si()) + int(!basis.empty());
}
