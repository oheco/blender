// SPDX-FileCopyrightText: 2026 Oheco contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <Python.h>
#include <string>

namespace blender::ohos {

enum class ExtensionsExitState { Ready, Pending, Error };

// Call only on Blender's Python/main WM thread, before destroying bpy/WM or
// entering Py_FinalizeEx. Stop new host/GUI work first. Keep this C++ state but
// never keep a PyObject across finalization. This gate does not finalize Python.
class ExtensionsShutdown {
 public:
  ExtensionsExitState begin() { return call("request_shutdown"); }
  ExtensionsExitState pump()
  {
    return call(admission_closed_ ? "pump" : "request_shutdown");
  }
  // Final pure-Python inventory check, after addon unregister and BEFORE bpy
  // binding teardown. Do not pump callbacks after removing their bpy context.
  ExtensionsExitState status() { return call("shutdown_status", true); }
  const std::string &error() const { return error_; }

 private:
  bool admission_closed_ = false;
  bool transport_seen_ = false;
  std::string error_;

  ExtensionsExitState python_error()
  {
    PyObject *exception = PyErr_GetRaisedException();
    PyObject *text = exception ? PyObject_Str(exception) : nullptr;
    const char *utf8 = text ? PyUnicode_AsUTF8(text) : nullptr;
    error_ = utf8 ? utf8 : "Extensions shutdown Python call failed";
    Py_XDECREF(text);
    Py_XDECREF(exception);
    PyErr_Clear();
    return ExtensionsExitState::Error;
  }

  ExtensionsExitState call(const char *method, bool pure = false)
  {
    error_.clear();
    if (Py_IsFinalizing()) {
      error_ = "Extensions shutdown gate must run before Python finalization";
      return ExtensionsExitState::Error;
    }
    if (!Py_IsInitialized()) {
      // Contract: only use this before finalization. No initialized interpreter
      // on initial/failed startup means no Extensions Python workers exist.
      return ExtensionsExitState::Ready;
    }
    const PyGILState_STATE gil = PyGILState_Ensure();
    ExtensionsExitState result = ExtensionsExitState::Error;
    PyObject *modules = PyImport_GetModuleDict();  // borrowed
    if (!PyDict_GetItemString(modules, "bl_pkg.bl_extension_worker")) {
      // The transport was never loaded. Do not initialize the whole addon
      // during an early/failed creator startup just to ask about absent work.
      if (transport_seen_) {
        error_ = "Owned Extensions transport module disappeared before finalization";
        PyGILState_Release(gil);
        return ExtensionsExitState::Error;
      }
      PyGILState_Release(gil);
      return ExtensionsExitState::Ready;
    }
    transport_seen_ = true;
    PyObject *module = nullptr;
    if (pure) {
      /* Final status must not import/reinitialize any addon after its hooks.
       * request_shutdown already loaded this owned status provider. */
      module = PyDict_GetItemString(modules, "bl_pkg.bl_extension_worker_ui");
      Py_XINCREF(module);
      if (!module) {
        error_ = "Owned Extensions status module disappeared before finalization";
        PyGILState_Release(gil);
        return ExtensionsExitState::Error;
      }
    }
    else {
      module = PyImport_ImportModule("bl_pkg.bl_extension_worker_ui");
    }
    PyObject *snapshot = module ? PyObject_CallMethod(module, method, nullptr) : nullptr;
    if (!snapshot) {
      result = python_error();
    }
    else if (!PyDict_Check(snapshot)) {
      error_ = "Extensions shutdown returned no status dictionary";
    }
    else {
      PyObject *ready = PyDict_GetItemString(snapshot, "ready");  // borrowed
      PyObject *accepting = PyDict_GetItemString(snapshot, "accepting");
      if (!ready || !PyBool_Check(ready) || !accepting || !PyBool_Check(accepting)) {
        error_ = "Extensions shutdown status lacks boolean ready/accepting";
      }
      else {
        admission_closed_ = accepting == Py_False;
        PyObject *errors = PyDict_GetItemString(snapshot, "hook_errors");
        if (errors && PyDict_Check(errors) && PyDict_Size(errors) != 0) {
          error_ = "Extensions lifecycle cleanup/delivery/callback error; ownership retained";
          result = ExtensionsExitState::Error;
        }
        else {
          /* transport_ready only permits operator finish; it NEVER grants teardown. */
          result = ready == Py_True && accepting == Py_False ? ExtensionsExitState::Ready : ExtensionsExitState::Pending;
        }
      }
    }
    Py_XDECREF(snapshot);
    Py_XDECREF(module);
    PyGILState_Release(gil);
    return result;
  }
};

}  // namespace blender::ohos
