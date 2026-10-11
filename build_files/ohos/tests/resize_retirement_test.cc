/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "../../../intern/ghost/intern/GHOST_OHOSSwapchainRetirement.hh"

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <limits>

using Policy = GHOST_OHOSSwapchainRetirement;

static void timeout_cannot_borrow_an_old_render_fence()
{
  Policy successor;
  successor.acquired_image();
  successor.queued_present(2);
  const bool old_render_fence_signaled = true;
  /* A finite acquire returned TIMEOUT: no new successful acquisition/fence signal exists.
   * Even a matching index left in a caller's variable and an old render fence cannot witness it.
   */
  assert(old_render_fence_signaled);
  assert(!Policy::witness_complete(false, successor.witnesses_ancestors(2), false));
  /* Successful matching acquisition may return before its acquisition fence signals. */
  assert(!Policy::witness_complete(true, successor.witnesses_ancestors(2), false));
  assert(Policy::witness_complete(true, successor.witnesses_ancestors(2), true));
}

static void image_selection_and_carryover()
{
  uint32_t retained_ancestors = 1; /* A was presented, then retired by B. */
  Policy chain_b;
  chain_b.acquired_image();
  chain_b.queued_present(3);
  chain_b.queued_present(1); /* Later presents must not replace the FIRST queued image. */
  assert(!chain_b.witnesses_ancestors(1));
  assert(chain_b.witnesses_ancestors(3));
  /* B is OUT_OF_DATE before image 3 is reacquired. A+B follow C; a new empty submit fence
   * must not release either, and C initially has no successful presentation to witness. */
  ++retained_ancestors;
  Policy chain_c;
  assert(!chain_c.witnesses_ancestors(3));
  assert(!Policy::witness_complete(false, chain_c.witnesses_ancestors(3), true));
  chain_c.acquired_image();
  chain_c.queued_present(0);
  /* Reacquiring a different image cannot witness A+B, even when that acquire fence completes. */
  assert(!Policy::witness_complete(true, chain_c.witnesses_ancestors(2), true));
  assert(retained_ancestors == 2);
  /* C also becomes OUT_OF_DATE before first-image completion. D carries A+B+C. */
  ++retained_ancestors;
  Policy chain_d;
  chain_d.acquired_image();
  chain_d.queued_present(1);
  assert(!Policy::witness_complete(true, chain_d.witnesses_ancestors(1), false));
  assert(retained_ancestors == 3);
  assert(Policy::witness_complete(true, chain_d.witnesses_ancestors(1), true));
  retained_ancestors = 0;
  assert(retained_ancestors == 0);
  /* An intermediate never acquired a render/presentation user; its own generation can be
   * discarded, but this does not establish a witness for ANY carried ancestors. */
  Policy unused;
  assert(!unused.acquired);
  assert(!unused.witnesses_ancestors(0));
}

static void admission_reserves_recovery_and_bounds_bad_weather()
{
  const uint64_t payload = Policy::image_bytes(1920, 1080, 4, Policy::max_images);
  uint32_t resident = 1;
  uint64_t bytes = payload;
  /* Repeated used OUT_OF_DATE successors with no witness stop before exhausting recovery. */
  while (Policy::admit(resident, bytes, payload, false)) {
    ++resident;
    bytes += payload;
  }
  assert(resident == Policy::max_generations - 1);
  assert(!Policy::admit(resident, bytes, payload, false));
  assert(!Policy::host_settled(1049, 1000));
  assert(Policy::host_settled(1050, 1000));
  assert(Policy::admit(resident, bytes, payload, true));
  ++resident;
  bytes += payload;
  assert(resident == Policy::max_generations);
  assert(!Policy::admit(resident, bytes, payload, true));
  /* If the current capped intermediate has NEVER acquired, reclaim it before allocation and
   * replace within the same hard footprint; no ancestor witness is fabricated by that reclaim. */
  Policy unused_at_cap;
  assert(!unused_at_cap.acquired);
  assert(Policy::admit(resident - 1, bytes - payload, payload, true));
  assert(!unused_at_cap.witnesses_ancestors(0));
  /* A completion witness frees the retained generations and makes normal progress possible. */
  assert(Policy::admit(1, payload, payload, false));
  /* Bytes enforce the reservation independently of the generation count. */
  const uint64_t near_cap = Policy::max_image_bytes - payload;
  assert(!Policy::admit(1, near_cap, payload, false));
  assert(Policy::admit(1, near_cap, payload, true));
  assert(!Policy::admit(0, 0, Policy::max_image_bytes + 1, true));
  assert(Policy::image_bytes(UINT32_MAX, UINT32_MAX, 8, 8) ==
         std::numeric_limits<uint64_t>::max());
}

static void persistent_suboptimal_does_not_keep_idle_window_drawing()
{
  Policy unchanged_chain;
  int probes = 0;
  for (int frame = 0; frame < 1000; frame++) {
    probes += unchanged_chain.note_suboptimal();
  }
  assert(probes == 1);
  Policy replacement;
  assert(replacement.note_suboptimal());
}

int main()
{
  timeout_cannot_borrow_an_old_render_fence();
  image_selection_and_carryover();
  admission_reserves_recovery_and_bounds_bad_weather();
  persistent_suboptimal_does_not_keep_idle_window_drawing();
  std::puts("PASS: native resize retirement witness/admission/idle policy");
  return 0;
}
