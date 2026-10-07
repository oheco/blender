/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef BASE_COVER_API_H
#define BASE_COVER_API_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* All allocation, dictionary state, worker threads, and exceptions belong to
 * the module. The driver passes only opaque handles, integers, and an errno
 * address encoded as a scalar for an accessor identity check. */
__attribute__((visibility("default"))) void *base_cover_create(unsigned iterations, unsigned codec_workers, unsigned notification_level);
__attribute__((visibility("default"))) int base_cover_start_internal(void *handle);
__attribute__((visibility("default"))) int base_cover_external(void *handle, unsigned index, uintptr_t driver_errno_address);
/* Each external index 0 and 1 participates exactly once after start_internal.
 * Lifecycle calls run on the owning main thread; no concurrent lifecycle calls
 * or destruction from a worker. Driver must join both external callers before
 * finish/destroy. finish joins
 * module-created threads; destroy returns only after all internal joins. */
__attribute__((visibility("default"))) int base_cover_finish(void *handle);
__attribute__((visibility("default"))) int base_cover_destroy(void *handle);
#ifdef __cplusplus
}
#endif
#endif
