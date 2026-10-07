/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include <Python.h>
namespace blender {
/* Initialization-only, with this interpreter's GIL already held. No process env,
 * compiled build-prefix certificate location, user filename interpolation or network.
 * certifi.where() follows the actual isolated runtime's resource relocation. */
inline bool bpy_ohos_tls_configure()
{
  PyObject *certifi = PyImport_ImportModule("certifi");
  PyObject *cafile = certifi ? PyObject_CallMethod(certifi, "where", nullptr) : nullptr;
  PyObject *globals = PyDict_New();
  bool ok = cafile && PyUnicode_Check(cafile) && globals;
  if (ok) {
    ok = PyDict_SetItemString(globals, "__builtins__", PyEval_GetBuiltins()) == 0 &&
         PyDict_SetItemString(globals, "_blender_ohos_cafile", cafile) == 0;
  }
  /* This fixed script contains no runtime data. The owned CA filename is a Python
   * Unicode VALUE in globals. Class methods belong to this interpreter only.
   * create_default_context/urllib use load_default_certs when no explicit CA is
   * supplied; explicit cafile/capath/cadata continue to use the stdlib path. */
  static constexpr const char *script = R"OHOS_TLS(
import ssl as _ssl
_probe_context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
_probe_context.load_verify_locations(cafile=_blender_ohos_cafile)
del _probe_context

def _blender_ohos_default_certs(self, purpose=_ssl.Purpose.SERVER_AUTH):
    if not isinstance(purpose, _ssl._ASN1Object):
        raise TypeError(purpose)
    self.load_verify_locations(cafile=_blender_ohos_cafile)

def _blender_ohos_verify_paths(self):
    self.load_verify_locations(cafile=_blender_ohos_cafile)

_ssl.SSLContext.load_default_certs = _blender_ohos_default_certs
_ssl.SSLContext.set_default_verify_paths = _blender_ohos_verify_paths
)OHOS_TLS";
  PyObject *result = ok ? PyRun_StringFlags(script, Py_file_input, globals, globals, nullptr) : nullptr;
  ok = result != nullptr;
  Py_XDECREF(result); Py_XDECREF(globals); Py_XDECREF(cafile); Py_XDECREF(certifi);
  if (!ok && !PyErr_Occurred()) { PyErr_SetString(PyExc_RuntimeError, "OHOS private Python CA initialization failed"); }
  return ok;
}
}
