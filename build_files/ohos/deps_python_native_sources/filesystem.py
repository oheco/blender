# SPDX-License-Identifier: GPL-2.0-or-later
"""Measure actual private filesystem permissions/case before source or HAP staging."""
import sys
sys.dont_write_bytecode=True
import os
from pathlib import Path
import stat
import tempfile
from io_utils import private_root,temporary_root,dump


def check(root,tmp):
    root=private_root(root);tmp=temporary_root(tmp);results=[]
    for base,label in ((root,'owned-cache'),(tmp,'private-TMPDIR')):
        if not base.is_dir():raise ValueError('Existing private test parent required')
        with tempfile.TemporaryDirectory(prefix='python-native-fs-proof-',dir=base) as td:
            d=Path(td);d.chmod(0o700)
            upper=d/'Case';upper.write_bytes(b'upper');upper.chmod(0o600)
            with (d/'case').open('xb') as f:f.write(b'lower')
            if upper.read_bytes()!=b'upper' or (d/'case').read_bytes()!=b'lower' or stat.S_IMODE(upper.stat().st_mode)!=0o600 or stat.S_IMODE(d.stat().st_mode)!=0o700:
                raise ValueError('Actual private filesystem case/permission capability required')
            results.append({'role':label,'device':d.stat().st_dev,'case_sensitive':True,'file_mode':oct(stat.S_IMODE(upper.stat().st_mode)),
                            'directory_mode':oct(stat.S_IMODE(d.stat().st_mode)),'temporary_probe_cleaned':True})
    result={'schema':1,'result':'PASS actual private filesystem IO only','checks':results,'native_compile':'NOTRUN','HAP':'NOTRUN'}
    dump(root/'receipts/filesystem-capabilities.json',result);return result
