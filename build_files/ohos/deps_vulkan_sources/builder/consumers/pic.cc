// SPDX-License-Identifier: GPL-2.0-or-later
#include <string>
// Link-only marker: these modules are never dlopened into an application.
extern "C" unsigned portable_vulkan_pic_marker(){return std::string("PIC").size();}
