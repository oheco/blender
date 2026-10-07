#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual in-process CPython71/NumPy19 + new pure8 gate. No child subprocesses.
The parent launches this with a NEW signed target; HAP may call evaluate with its
actual PyConfig/importer paths. Installed HAP/Ark behavior remains a separate gate.
"""
import sys
sys.dont_write_bytecode=True
_STARTUP_MODULES={name:{'spec_origin':getattr(getattr(module,'__spec__',None),'origin',None),
                       'file':getattr(module,'__file__',None),
                       'frozen':getattr(getattr(module,'__spec__',None),'origin',None)=='frozen',
                       'builtin':getattr(getattr(module,'__spec__',None),'origin',None)=='built-in'}
                  for name,module in tuple(sys.modules.items()) if module is not None}
import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import math
import os
import threading
HERE=Path(__file__).resolve().parent


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


# The terminal launcher uses -I, so its script directory is not on sys.path.
resource_origins=load(HERE/'resource_origins.py','native_resource_origins')


def no_subprocess(event,args):
    if event in ('subprocess.Popen','os.system','os.posix_spawn','os.fork','os.exec','os.spawn'):raise RuntimeError('This embedded/native mapping gate forbids child processes')


def evaluate(root,context='terminal',native_dir=None,pure_dir=None,*,startup_modules=None):
    root=Path(root).resolve();runtime=root/'runtime'
    if sys.version_info[:3]!=(3,13,13) or sys.platform!='ohos':raise RuntimeError('Real target CPython3.13.13 required')
    sys.addaudithook(no_subprocess)
    mapping=json.loads((root/'hap-native/python-modules.json').read_text());modules=mapping['modules']
    if len(modules)!=90:raise RuntimeError('Exact native71+19 mapping required')
    if native_dir is not None:
        native=resource_origins.canonical_path(native_dir);pure=resource_origins.canonical_path(pure_dir)
        sys.path[:]=[str(native),str(pure),str(pure/'site-packages'),str(HERE)]
        importer=load(HERE/'python/helpers/trusted_native_importer.py','native_trusted_importer')
        importer.install(native,modules)
    else:
        native=runtime/'lib/python3.13/lib-dynload';pure=runtime/'lib/python3.13'
    loaded=[]
    for name,basename in modules.items():
        module=importlib.import_module(name);path=Path(module.__file__).resolve()
        expected=(Path(native_dir)/basename).resolve() if native_dir is not None else (runtime/'lib/python3.13/site-packages'/Path(*name.split('.')[:-1])/basename).resolve() if name.startswith('numpy.') else (native/basename).resolve()
        if path!=expected:raise RuntimeError('Real module origin differs: '+name)
        loaded.append(name)
    selected=json.loads((HERE/'resources/selected-pure8.lock.json').read_text())
    raw_origins=resource_origins.verify_resources(pure,HERE.parents[2],selected)
    import sysconfig,ssl,ctypes,sqlite3,zlib,bz2,lzma,hashlib
    assert sysconfig.get_config_var('INSTSONAME')=='libpython3.13.so'
    assert sysconfig.get_config_var('SOABI')=='cpython-313-aarch64-linux-ohos'
    assert sys._is_gil_enabled() and not sysconfig.get_config_var('Py_GIL_DISABLED')
    data=b'actual native Python geometry resource\x00'*257
    for codec in (zlib,bz2,lzma):assert codec.decompress(codec.compress(data))==data
    assert len(hashlib.sha256(data).digest())==32
    assert ssl.OPENSSL_VERSION.startswith('OpenSSL 3.5.8')
    assert len(ssl.RAND_bytes(64))==64
    ca=ssl.create_default_context();assert ca.verify_mode==ssl.CERT_REQUIRED and ca.check_hostname and ca.get_ca_certs()
    connection=sqlite3.connect(':memory:');connection.execute('create virtual table data using fts5(text)');connection.execute("insert into data values ('native SDK math')")
    assert connection.execute("select count(*) from data where data match 'native'").fetchone()==(1,);connection.close()
    callback=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_int)(lambda n:n+12);assert callback(30)==42
    assert math.isclose(math.sin(math.pi/6),0.5,rel_tol=1e-14) and math.isfinite(math.exp(1.0))
    values=[];worker=threading.Thread(target=lambda:values.append(sum(range(10000))));worker.start();worker.join(15)
    assert not worker.is_alive() and values==[49995000]
    accepted=load(HERE/'numpy/acceptance.py','numpy_native_acceptance')
    numpy_result=accepted.evaluate(runtime,pure.parents[1] if native_dir else runtime,libpython=(Path(native_dir)/'libpython3.13.so') if native_dir else runtime/'lib/libpython3.13.so',
        expected_executable=runtime/'bin/python3.13' if context=='terminal' else None,context=context,
        native_dir=native_dir,module_manifest=root/'hap-native/python-modules.json' if native_dir else None)
    pure_versions=raw_origins['pure_versions']
    import requests,urllib3,idna,certifi,charset_normalizer,attrs,cattrs,typing_extensions
    assert idna.encode('例子.测试')==b'xn--fsqu00a.xn--0zwm56d'
    assert Path(certifi.where()).is_file() and ssl.create_default_context(cafile=certifi.where()).get_ca_certs()
    from dataclasses import dataclass
    @dataclass
    class Resource:count:int;name:str
    converter=cattrs.Converter();obj=converter.structure({'count':42,'name':'Unicode geometry 迁移'},Resource)
    assert converter.unstructure(obj)=={'count':42,'name':'Unicode geometry 迁移'}
    return {'schema':1,'result':'PASS actual in-process native90+new selectedpure8 only','native_modules':loaded,'native_extension_count':90,
            'numpy':numpy_result,'pure_versions':pure_versions,'raw_resource_origins':raw_origins,
            'checks':['regular GIL/native ABI','compression/hash','SSL RNG/real CA','SQLite FTS5','signed static ffi callback','normal SDK math/thread','NumPy array/linalg/FFT/random/interfaces/FPU','exact non-frozen ssl source','selected pure8 source origins and original PKG-INFO metadata/CA hashes','new8pure IDNA+cattrs'],
            'startup_modules':{'capture':'flat CLI before path isolation' if startup_modules is not None else 'acceptance entry before shared imports',
                               'modules':startup_modules if startup_modules is not None else _STARTUP_MODULES,
                               'raw_home_initialization_proven':False,
                               'scope':'Terminal bootstrap may include runtime encodings and frozen os; recorded without origin rewriting'},
            'subprocesses_launched':0,'network_requests':0,'context':context,'actual_installed_HAP':'NOTRUN unless separate installed app receipt supplied',
            'HAP_PyConfig_startup':'NOTRUN: CLI imports occur after terminal interpreter initialization',
            'Blender_postlink':'NOTRUN separate parent gate'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--native-dir',type=Path);p.add_argument('--pure-dir',type=Path)
    a=p.parse_args()
    if a.report.exists():raise ValueError('New absent report required')
    result=evaluate(a.root,'terminal-hap-layout' if a.native_dir else 'terminal',a.native_dir,a.pure_dir)
    a.report.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n');print(json.dumps({'result':result['result'],'extensions':90,'selected_pure':8}))

if __name__=='__main__':main()
