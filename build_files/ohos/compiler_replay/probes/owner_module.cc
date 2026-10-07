// SPDX-License-Identifier: GPL-2.0-or-later
#include <string>
#include <vector>
#include <stdexcept>
struct Owner{std::string text="module-owned";std::vector<int> values{3,5,7};};
extern "C" __attribute__((visibility("default"))) void* toolkit_create(){try{return new Owner;}catch(...){return nullptr;}}
extern "C" __attribute__((visibility("default"))) int toolkit_check(void*p){if(!p)return 1;try{auto&o=*static_cast<Owner*>(p);if(o.text!="module-owned"||o.values[1]!=5)return 2;throw std::runtime_error("module");}catch(const std::runtime_error&e){return std::string(e.what())!="module";}catch(...){return 3;}}
extern "C" __attribute__((visibility("default"))) void toolkit_destroy(void*p){delete static_cast<Owner*>(p);}
