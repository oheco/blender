// SPDX-License-Identifier: GPL-2.0-or-later
#include <ImathConfig.h>
#include <cstdio>
#include <cstring>
int main(){if(std::strcmp(IMATH_VERSION_STRING,"3.2.2"))return 1;std::puts("PASS independently loaded Imath::Config header/property alias");return 0;}
