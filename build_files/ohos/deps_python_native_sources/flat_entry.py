#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Isolate flat imports; record terminal bootstrap separately from raw resources."""
import sys
sys.dont_write_bytecode=True
# CLI: NEW_RUNTIME/bin/python3.13 -I -S flat_entry.py OWN_ROOT NEW_REPORT
# The parent scopes the loader to the mapped native directory. Python startup
# already occurred: encodings may be from the terminal runtime and os may be
# frozen. This CLI entry cannot prove raw-home PyConfig initialization in a HAP.
startup_modules={name:{'spec_origin':getattr(getattr(module,'__spec__',None),'origin',None),
                       'file':getattr(module,'__file__',None),
                       'frozen':getattr(getattr(module,'__spec__',None),'origin',None)=='frozen',
                       'builtin':getattr(getattr(module,'__spec__',None),'origin',None)=='built-in'}
                 for name,module in tuple(sys.modules.items()) if module is not None}
root=sys.argv[1].rstrip('/')
native=root+'/hap-native/libs/arm64-v8a'
pure=root+'/hap-resources/resources/rawfile/python/lib/python3.13'
# Replace the entire list before argparse/json/math or other shared imports.
# Inherited terminal stdlib/site paths would hide missing raw resources.
sys.path[:]=[native,pure,pure+'/site-packages',__file__.rsplit('/',1)[0]]
import native_acceptance
import json
from pathlib import Path
report=Path(sys.argv[2])
if report.exists():raise ValueError('Absent flat acceptance report required')
result=native_acceptance.evaluate(root,'terminal-hap-layout',native,pure,startup_modules=startup_modules)
report.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'result':result['result'],'extensions':90,'selectedpure':8,'installed_HAP':'NOTRUN','HAP_PyConfig_startup':'NOTRUN'}))
