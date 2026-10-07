// SPDX-License-Identifier: GPL-2.0-or-later
#include <version>
#include <string>
#include <stdexcept>
#include <cstdio>
#include <cstring>
#define S1(a) #a
#define S(a) S1(a)
int main(){static_assert(__cplusplus==201703L);static_assert(_LIBCPP_VERSION==15004);if(std::strcmp(S(_LIBCPP_ABI_NAMESPACE),"__n1"))return 1;try{throw std::runtime_error("cpp17");}catch(const std::exception&e){if(std::string(e.what())!="cpp17")return 2;}std::puts("PASS real C++17 SDK15004 __n1 exceptions");return 0;}
