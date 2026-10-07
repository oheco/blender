// SPDX-License-Identifier: GPL-2.0-or-later
#include <dlfcn.h>
#include <cstdio>
int main(int argc,char**argv){if(argc!=2)return 1;for(int cycle=0;cycle<3;++cycle){void*h=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);if(!h){std::fprintf(stderr,"%s\n",dlerror());return 2;}auto create=reinterpret_cast<void*(*)()>(dlsym(h,"toolkit_create"));auto check=reinterpret_cast<int(*)(void*)>(dlsym(h,"toolkit_check"));auto destroy=reinterpret_cast<void(*)(void*)>(dlsym(h,"toolkit_destroy"));if(!create||!check||!destroy)return 3;void*p=create();if(!p||check(p))return 4;destroy(p);if(dlclose(h))return 5;}std::puts("PASS real three dlopen/create/check/destroy/dlclose cycles");return 0;}
