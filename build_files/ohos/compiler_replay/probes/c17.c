/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
#ifndef __OHOS__
#error Real OHOS required
#endif
#ifndef __aarch64__
#error Real AArch64 required
#endif
_Static_assert(__STDC_VERSION__==201710L,"C17 required");
int main(void){atomic_int value=1;atomic_fetch_add(&value,2);char*p=malloc(64);if(!p)return 1;strcpy(p,"native-sdk");int ok=!strcmp(p,"native-sdk")&&atomic_load(&value)==3;free(p);puts("PASS native C17 malloc/string/atomic");return !ok;}
