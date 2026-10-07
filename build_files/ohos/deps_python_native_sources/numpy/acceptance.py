#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real native NumPy core/LAPACK/FFT/RNG/FFI/interfaces/math/FPU/GIL acceptance.
No subprocess, shell, network, compilation, or native-path rewriting. HAP can call
 evaluate(..., context='hap-embedded', native_dir=..., module_manifest=...) after
 the common sealed importer/PyConfig has installed the actual app paths.
Historical receipt files are never read as acceptance outcomes.
"""
import sys
sys.dont_write_bytecode=True
import argparse
import ctypes
import importlib
import importlib.util
import json
from pathlib import Path
import threading

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('_numpy_acceptance_elf',HERE/'elf.py')
elf=importlib.util.module_from_spec(spec);spec.loader.exec_module(elf)


def require(condition,message):
    if not condition:raise RuntimeError(message)


def _loaded_python():
    # libc loader enumeration works without /proc and without spawning anything in HAP.
    class Info(ctypes.Structure):
        _fields_=[('addr',ctypes.c_uint64),('name',ctypes.c_char_p),('phdr',ctypes.c_void_p),('phnum',ctypes.c_uint16)]
    callback_type=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.POINTER(Info),ctypes.c_size_t,ctypes.c_void_p)
    names=[]
    def visit(info,size,data):
        if size>=ctypes.sizeof(Info) and info.contents.name:
            name=info.contents.name.decode('utf-8',errors='strict')
            if Path(name).name.startswith('libpython3.13.so'):names.append(name)
        return 0
    callback=callback_type(visit);function=ctypes.CDLL(None).dl_iterate_phdr
    function.argtypes=[callback_type,ctypes.c_void_p];function.restype=ctypes.c_int
    require(function(callback,None)==0,'Actual loader enumeration failed')
    require(len(names)==1 and Path(names[0]).name=='libpython3.13.so','One genuine unversioned shared Python runtime required: '+repr(names))
    return names


class ArrayInterface(ctypes.Structure):
    # Exact NumPy ndarraytypes.h PyArrayInterface layout, including normal ABI padding.
    _fields_=[('two',ctypes.c_int),('nd',ctypes.c_int),('typekind',ctypes.c_char),('itemsize',ctypes.c_int),('flags',ctypes.c_int),
              ('shape',ctypes.POINTER(ctypes.c_ssize_t)),('strides',ctypes.POINTER(ctypes.c_ssize_t)),('data',ctypes.c_void_p),('descr',ctypes.c_void_p)]


def evaluate(runtime_prefix,numpy_prefix,libpython=None,expected_executable=None,*,context='terminal',native_dir=None,module_manifest=None):
    require(context in ('terminal','hap-embedded','terminal-hap-layout'),'Unknown explicit acceptance context')
    runtime_prefix=Path(runtime_prefix).resolve();numpy_prefix=Path(numpy_prefix).resolve();site=numpy_prefix/'lib/python3.13/site-packages'
    libpython=Path(libpython or runtime_prefix/'lib/libpython3.13.so').resolve()
    require(sys.version_info[:3]==(3,13,13) and sys.platform=='ohos','Actual native CPython3.13.13/OHOS required')
    import sysconfig
    require(sysconfig.get_config_var('SOABI')=='cpython-313-aarch64-linux-ohos','Wrong actual target SOABI')
    require(sysconfig.get_config_var('INSTSONAME')=='libpython3.13.so','Versioned/renamed Python runtime rejected')
    require(sysconfig.get_config_var('Py_GIL_DISABLED') in (None,0) and sys._is_gil_enabled(),'Regular GIL profile required')
    if expected_executable is not None:require(Path(sys.executable).resolve()==Path(expected_executable).resolve(),'Interpreter resolved outside fresh runtime')
    python_elf=elf.python_library(libpython);loaded_before=_loaded_python()
    if Path(loaded_before[0]).is_absolute():require(Path(loaded_before[0]).resolve()==libpython,'Loaded Python object has wrong runtime origin')
    python_handle=ctypes.PyDLL(str(libpython))
    require(ctypes.cast(python_handle.Py_GetVersion,ctypes.c_void_p).value==ctypes.cast(ctypes.pythonapi.Py_GetVersion,ctypes.c_void_p).value,
            'Loaded PyRuntime symbols differ from the interpreter')
    ctypes.pythonapi.PyGILState_Check.argtypes=[];ctypes.pythonapi.PyGILState_Check.restype=ctypes.c_int
    require(ctypes.pythonapi.PyGILState_Check()==1,'Python acceptance entry lacks the GIL')
    if context=='terminal':sys.path.insert(0,str(site))
    # HAP caller owns PyConfig/trusted_native_importer; do not fake a package mapping.
    import numpy as np
    require(np.__version__=='2.3.4' and Path(np.__file__).resolve().is_relative_to(site/'numpy'),'Wrong NumPy source/package origin')
    config=np.__config__.show(mode='dicts')
    for language in ('c','c++'):
        require(config['Compilers'][language]['name']=='clang' and config['Compilers'][language]['version']=='15.0.4','SDK15 compiler provenance differs')
    machine=config['Machine Information'];require(not machine.get('cross-compiled',False),'Cross/faked probe profile rejected')
    for kind in ('host','build'):
        require(machine[kind]['system']=='ohos' and machine[kind]['family']=='aarch64','Actual NumPy native machine provenance differs')
    for provider in ('blas','lapack'):
        dependency=config['Build Dependencies'][provider]
        require(dependency['name']=='none' and not dependency.get('found',False),'Expected complete bundled no-external-BLAS/LAPACK source profile')
    expected=json.loads((HERE/'profile.json').read_text())['accepted_extensions'];extensions=[];audit=[]
    native_dir=Path(native_dir).resolve() if native_dir is not None else None
    mapping=json.loads(Path(module_manifest).read_text())['modules'] if module_manifest is not None else None
    if context!='terminal':require(native_dir is not None and mapping is not None,'HAP mapping must be supplied by the real sealed parent layout')
    for name in expected:
        module=importlib.import_module(name);file=Path(module.__file__).resolve()
        if native_dir is None:
            require(file.name.endswith('.cpython-313-aarch64-linux-ohos.so'),'Wrong terminal NumPy extension ABI filename')
            require(file.is_relative_to(site/'numpy'),'Native NumPy extension escaped selected fresh prefix')
        else:
            require(name in mapping and isinstance(mapping[name],str) and Path(mapping[name]).name==mapping[name] and file.suffix=='.so','Invalid flat NumPy module mapping')
            require(file==native_dir/mapping[name],'Extension resolved outside exact HAP native mapping')
        value=elf.extension(file);value['module']=name;audit.append(value);extensions.append(name)
    require(len(extensions)==19 and len({r['path'] for r in audit})==19,'Exactly 19 distinct real NumPy extensions required')
    if native_dir is None:
        require({str(p.resolve()) for p in (site/'numpy').rglob('*.so')}=={r['path'] for r in audit},'Extra or missing installed NumPy extensions')
    checks=['all 19 real extensions import with signed AArch64/unversioned Python/system closure and no RPATH/TEXTREL']
    libc=ctypes.CDLL(None);libc.fegetround.argtypes=[];libc.fegetround.restype=ctypes.c_int
    round_before=libc.fegetround();require(round_before>=0,'Actual libc floating-point rounding control unavailable')
    error_before=np.geterr().copy()
    array=np.arange(24,dtype=np.float64).reshape(4,6)
    require(array.shape==(4,6) and array[3,5]==23,'dtype/shape failure')
    require(np.array_equal(array[:,::2].copy(),[[0,2,4],[6,8,10],[12,14,16],[18,20,22]]),'Strided copy failure')
    require(np.array_equal(np.frombuffer(np.array([1,2,3],dtype='<i8').tobytes(),dtype='<i8'),[1,2,3]),'Endian buffer roundtrip failure')
    checks.append('dtype/shape/strided-copy/endian buffer core')
    matrix=np.array([[4.,1.,0.],[1.,3.,1.],[0.,1.,2.]]);vector=np.array([1.,2.,3.]);solution=np.linalg.solve(matrix,vector)
    require(np.allclose(matrix@solution,vector,rtol=1e-12,atol=1e-12),'Portable LAPACK solve failure')
    require(np.allclose(matrix@np.linalg.inv(matrix),np.eye(3),rtol=1e-12,atol=1e-12),'Portable LAPACK inverse failure')
    u,s,v=np.linalg.svd(matrix);require(np.allclose((u*s)@v,matrix,rtol=1e-12,atol=1e-12),'Portable LAPACK SVD failure')
    w,e=np.linalg.eigh(matrix);require(np.allclose(matrix@e,e*w,rtol=1e-12,atol=1e-12),'Portable LAPACK eigh failure')
    require(np.isclose(np.linalg.det(matrix),18.) and np.isclose(np.dot(vector,vector),14.),'Determinant/bundled BLAS dot failure')
    try:np.linalg.solve(np.array([[1.,2.],[2.,4.]]),np.ones(2))
    except np.linalg.LinAlgError:pass
    else:raise RuntimeError('Singular LAPACK input must fail')
    checks.append('actual bundled LAPACK solve/inverse/SVD/eigh/determinant/singular error and BLAS dot')
    signal=np.sin(np.arange(32)*.37)+1j*np.cos(np.arange(32)*.23)
    require(np.allclose(np.fft.ifft(np.fft.fft(signal)),signal,rtol=1e-12,atol=1e-12),'Complex pocketfft roundtrip failure')
    require(np.allclose(np.fft.irfft(np.fft.rfft(signal.real)),signal.real,rtol=1e-12,atol=1e-12),'Real pocketfft roundtrip failure')
    checks.append('actual C++17 pocketfft complex/real roundtrips')
    rng=np.random.default_rng(123456);values=rng.normal(size=128)
    require(np.array_equal(values,np.random.default_rng(123456).normal(size=128)) and np.isfinite(values).all() and len(np.unique(values))==128,'RNG reproducibility/distribution failure')
    integers=rng.integers(1,6,size=512);require(np.all((integers>=1)&(integers<6)),'RNG integer range failure')
    for cls in (np.random.MT19937,np.random.Philox,np.random.PCG64,np.random.SFC64):
        one=np.random.Generator(cls(42)).integers(0,2**31,size=32);two=np.random.Generator(cls(42)).integers(0,2**31,size=32)
        require(np.array_equal(one,two),'Real bit-generator reproducibility failed '+cls.__name__)
    checks.append('normal/integer distributions and MT19937/Philox/PCG64/SFC64 reproducibility')
    values=np.array([0.,.5,1.,2.]);require(np.allclose(np.sin(values),[0.,.479425538604203,.8414709848078965,.9092974268256817]),'Trigonometric ufunc failure')
    require(np.allclose(np.log(np.exp(values)),values),'exp/log ufunc failure')
    for operation in (lambda:np.divide(np.ones(2),np.zeros(2)),lambda:np.sqrt(np.array([-1.])),lambda:np.exp(np.array([1000.]))):
        with np.errstate(divide='raise',invalid='raise',over='raise'):
            try:operation()
            except FloatingPointError:pass
            else:raise RuntimeError('Ufunc FPU exception path silently succeeded')
    with np.errstate(under='ignore'):
        subnormal=np.nextafter(np.float64(0.),np.float64(1.));require(subnormal>0 and subnormal*2>subnormal,'AArch64 FPU flush-to-zero/subnormal mismatch')
    require(np.geterr()==error_before and libc.fegetround()==round_before,'FPU rounding/error mode changed')
    checks.append('math ufuncs, divide/invalid/overflow errors, real libc FPU rounding preservation and subnormals')
    pointer=array.ctypes.data_as(ctypes.POINTER(ctypes.c_double));require(pointer[0]==0.,'ctypes data origin mismatch');pointer[0]=42.
    require(array[0,0]==42. and np.ctypeslib.as_array(pointer,shape=(24,))[0]==42.,'ctypes shared data ownership mismatch')
    callback=ctypes.CFUNCTYPE(ctypes.c_double,ctypes.c_double)(lambda x:x+1.)
    require(callback(float(array[0,0]))==43.,'Actual signed/static libffi callback failure')
    interface=array.__array_interface__;require(interface['version']==3 and interface['shape']==array.shape and interface['data'][0]==array.ctypes.data,'Python array interface mismatch')
    capsule=array.__array_struct__;get_pointer=ctypes.pythonapi.PyCapsule_GetPointer;get_pointer.argtypes=[ctypes.py_object,ctypes.c_char_p];get_pointer.restype=ctypes.c_void_p
    structure=ctypes.cast(get_pointer(capsule,None),ctypes.POINTER(ArrayInterface)).contents
    require(structure.two==2 and structure.nd==2 and structure.typekind==b'f' and structure.itemsize==8 and structure.data==array.ctypes.data,'Real C array interface mismatch')
    require(tuple(structure.shape[i] for i in range(2))==array.shape and tuple(structure.strides[i] for i in range(2))==array.strides,'C array interface shape/stride mismatch')
    buffer=memoryview(array);require(buffer.shape==array.shape and buffer.strides==array.strides and buffer.itemsize==8,'PEP3118 interface mismatch')
    require(np.shares_memory(np.asarray(buffer),array),'PEP3118 buffer ownership mismatch')
    checks.append('live ctypes pointer/static libffi callback/Python and C array interfaces/PEP3118 ownership')
    failures=[];completed=[]
    def threaded(seed):
        try:
            require(ctypes.pythonapi.PyGILState_Check()==1,'Worker Python entry lacks GIL')
            a=np.random.default_rng(seed).normal(size=(16,16));b=a@a.T+np.eye(16)
            require(np.allclose(b@np.linalg.solve(b,np.ones(16)),np.ones(16),rtol=1e-9,atol=1e-9),'Concurrent LAPACK failure')
            require(np.allclose(np.fft.ifft(np.fft.fft(a,axis=0),axis=0).real,a),'Concurrent FFT failure');completed.append(seed)
        except BaseException as error:failures.append(repr(error))
    threads=[threading.Thread(target=threaded,args=(seed,),daemon=True) for seed in range(4)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(30)
    require(not failures and len(completed)==4 and not any(t.is_alive() for t in threads),'Actual regular-GIL threaded NumPy failure '+repr(failures))
    require(ctypes.pythonapi.PyGILState_Check()==1 and sys._is_gil_enabled(),'Regular GIL changed after NumPy/FFI/thread entry')
    require(libc.fegetround()==round_before and np.geterr()==error_before,'Post-thread FPU mode changed')
    checks.append('regular GIL at interpreter/callback/worker entries and concurrent native LAPACK/FFT')
    loaded_after=_loaded_python();require(loaded_after==loaded_before,'Additional shared PyRuntime appeared after imports/FFI')
    return {'schema_version':1,'result':'PASS','context':context,'python':sys.version,'platform':sys.platform,'SOABI':sysconfig.get_config_var('SOABI'),
            'INSTSONAME':sysconfig.get_config_var('INSTSONAME'),'numpy':np.__version__,'checks':checks,'extensions':extensions,'elf_audit':audit,
            'runtime_origin':{'python_prefix':str(runtime_prefix),'python_executable':sys.executable,'libpython':python_elf,'loaded_objects':loaded_after,
                              'numpy_package':np.__file__,'numpy_prefix':str(numpy_prefix),'numpy_build_config':config,
                              'fallback_source':'NumPy original numpy/linalg/meson.build selects complete bundled f2c BLAS/LAPACK-lite when !have_lapack'},
            'fpu':{'libc_fegetround_before':round_before,'libc_fegetround_after':libc.fegetround(),'subnormal_nonzero':True,
                   'NumPy_get_fpu_mode':'Upstream returns None on AArch64; actual libc rounding/subnormal/ufunc checks used'},
            'performance':'Original complete bundled BLAS/LAPACK-lite fallback; may be much slower than optimized OpenBLAS',
            'HAP_install_execution':'Embedded invocation alone is not an installation receipt; parent must bind actual HAP launch/package/app.loadpolicy evidence',
            'not_claimed':['complete NumPy regression suite','Blender application','optimized BLAS performance','cryptographic certificate-chain trust']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-prefix',required=True);parser.add_argument('--numpy-prefix',required=True);parser.add_argument('--libpython',required=True)
    parser.add_argument('--expected-executable');parser.add_argument('--report',required=True);parser.add_argument('--context',choices=['terminal','hap-embedded','terminal-hap-layout'],default='terminal')
    parser.add_argument('--native-dir');parser.add_argument('--module-manifest');args=parser.parse_args()
    result=evaluate(args.runtime_prefix,args.numpy_prefix,args.libpython,args.expected_executable,context=args.context,native_dir=args.native_dir,module_manifest=args.module_manifest)
    report=Path(args.report)
    if report.exists() or report.is_symlink():raise RuntimeError('Acceptance report must be a new output, never overwrite an accepted receipt')
    report.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
