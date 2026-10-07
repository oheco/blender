# SPDX-License-Identifier: GPL-2.0-or-later
"""New owned private roots, explicit environments and real command receipts."""
import sys
sys.dont_write_bytecode=True
import fcntl
import hashlib
import re
import struct
import traceback
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import time
from source_guard import sha,HERE
OWNER='.python-native-builder-owned.json'
NAMESPACE='build_files/ohos/deps_python_native_sources'


def real_path(value):
    p=Path(value)
    if not p.is_absolute() or '..' in p.parts or any(ord(c)<32 or ord(c)==127 or c in ':\\\\' for c in str(p)):raise ValueError('Absolute canonical caller path required')
    for q in (p,*p.parents):
        if q.is_symlink():raise ValueError('Caller path contains a symlink')
    return p.resolve()


def private_root(value):
    cache=real_path(os.environ['XDG_CACHE_HOME']);p=real_path(value)
    if not p.is_relative_to(cache) or p==cache or not p.name.startswith('blender-ohos-python-native-'):
        raise ValueError('Use new cache/blender-ohos-python-native-* namespace')
    return p


def temporary_root(value):
    actual=real_path(os.environ['TMPDIR']);p=real_path(value)
    if not p.is_dir() or not p.is_relative_to(actual):raise ValueError('Temporary files require existing real TMPDIR')
    return p


def dump(path,data):
    p=Path(path)
    if p.is_symlink():raise ValueError('Receipt symlink refused')
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data,indent=2,sort_keys=True,ensure_ascii=False)+'\n')


def regular_bytes(path,limit=None):
    """Read only a regular object through real ancestors; never follow a link."""
    path=Path(path);real_path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Regular context/target required')
        return stream.read() if limit is None else stream.read(limit)


def owned_marker(root):
    """Marker authority alone, deliberately independent of output validation."""
    root=private_root(root)
    expected={'schema':1,'owner':NAMESPACE,'inputs_lock_sha256':sha(HERE/'inputs.lock.json')}
    if json.loads(regular_bytes(root/OWNER))!=expected:raise ValueError('Unowned or differently sealed root refused')
    return root


# Exact regular-GIL 3.13.13 profile observed in the genuine source build.
# Names alone never authorize a link: all context, producer and target checks follow.
SHARED_NAMES=tuple('array _asyncio _bisect _contextvars _csv _heapq _json _lsprof _opcode _pickle _queue _random _struct _interpreters _interpchannels _interpqueues _zoneinfo math cmath _statistics _datetime _decimal binascii _bz2 _lzma zlib _md5 _sha1 _sha2 _sha3 _blake2 pyexpat _elementtree _codecs_cn _codecs_hk _codecs_iso2022 _codecs_jp _codecs_kr _codecs_tw _multibytecodec unicodedata fcntl grp mmap _posixsubprocess resource select _socket syslog termios _posixshmem _multiprocessing _ctypes _sqlite3 _ssl _hashlib xxsubtype _xxtestfuzz _testbuffer _testinternalcapi _testcapi _testlimitedcapi _testclinic _testclinic_limited _testimportmultiple _testmultiphase _testsinglephase _testexternalinspection _ctypes_test xxlimited xxlimited_35'.split())
BUILD_LIB='build/lib.ohos-aarch64-3.13'
EXT_SUFFIX='.cpython-313-aarch64-linux-ohos.so'


def selected_make(root,tools):
    """Bind the recorded selection to its original owned readonly Runner evidence.

    Current pinned bytes are a continuity check, never a substitute for that
    selection/version receipt. Keep byte_check's absolute-path/alias semantics.
    """
    root=owned_marker(root)
    profile=json.loads(regular_bytes(root/'receipts/readonly-tool-profile.json'))
    if not isinstance(profile,dict) or profile.get('schema')!=1 or profile.get('result')!='PASS readonly tools/bytes/preprocessor only' or not isinstance(profile.get('tools'),dict) or set(profile['tools'])!=set(tools['tools']):
        raise ValueError('Successful owned readonly tool profile required')
    selected=profile['tools']['make'];versions=profile.get('versions',{})
    if not isinstance(selected,str) or not Path(selected).is_absolute() or not isinstance(versions,dict) or not isinstance(versions.get('make'),str) or 'GNU Make 4.4.1' not in versions['make']:
        raise ValueError('Recorded absolute pinned Make selection/version required')
    record=json.loads(regular_bytes(root/'logs/readonly-make.json'))
    log=regular_bytes(root/'logs/readonly-make.log')
    expected=('$ '+shlex.join([selected,'--version'])+'\n'+versions['make']+'\nexit_code=0\n').encode()
    if not isinstance(record,dict) or record.get('argv')!=[selected,'--version'] or record.get('cwd')!=str(root) or record.get('exit_code')!=0 or record.get('log_sha256')!=hashlib.sha256(log).hexdigest() or log!=expected:
        raise ValueError('Original owned readonly Make Runner selection/log differs')
    scope=record.get('scoped_env',{});seconds=record.get('seconds')
    ninja=profile['tools']['ninja'];perl=profile['tools']['perl']
    if not all(isinstance(value,str) and Path(value).is_absolute() for value in (ninja,perl)):
        raise ValueError('Recorded readonly environment tool selections differ')
    # Match the literal ordered segment emitted by toolchain.validate, rather
    # than splitting caller paths: byte_check permits aliases containing ':'.
    path_segment=':'+str(Path(selected).parent)+':'+str(Path(ninja).parent)+':'
    perl_base=Path(perl).parent.parent/'lib/5.44.0'
    perl_path=str(perl_base)+':'+str(perl_base/'aarch64-linux')
    if not isinstance(scope,dict) or set(scope)!={'PATH','PERL5LIB','PIP_NO_INDEX','PIP_DISABLE_PIP_VERSION_CHECK'} or not isinstance(scope['PATH'],str) or path_segment not in scope['PATH'] or scope['PERL5LIB']!=perl_path or scope['PIP_NO_INDEX']!='1' or scope['PIP_DISABLE_PIP_VERSION_CHECK']!='1' or type(seconds) not in (int,float) or not 0<=seconds<float('inf'):
        raise ValueError('Original readonly Make Runner context missing/different')
    # Deliberately follow a caller tool alias just as toolchain.byte_check does;
    # receipts and owned outputs above still require real regular paths.
    path=Path(selected);pin=tools['tools']['make']
    if not path.is_file() or path.stat().st_size!=pin['size'] or sha(path)!=pin['sha256']:
        raise ValueError('Selected Make current pinned bytes differ')
    return selected


def module_alias_context(root):
    """Supported genuine CPython owner/configure/build context, using file I/O only."""
    root=owned_marker(root);objects=root/'cpython'
    makebytes=regular_bytes(objects/'Makefile');make=makebytes.decode()
    binding=json.loads(regular_bytes(root/'receipts/native-configure-generator-binding.json'))
    tools=json.loads((HERE/'tools.lock.json').read_text())
    if binding.get('schema')!=1 or binding.get('generated_Makefile_sha256')!=hashlib.sha256(makebytes).hexdigest() or binding.get('tool_lock_sha256')!=sha(HERE/'tools.lock.json') or binding.get('pinned_generator_binary_sha256')!=tools['tools']['regen_python']['sha256']:
        raise ValueError('Generated Makefile/generator receipt binding differs')
    contract=binding['actual_native_configure_and_generated_Makefile']
    if contract.get('native_cross_compiling')!='no' or contract.get('regen_version')!='Python 3.13.13' or contract.get('generator_environment_and_Makefile_same_absolute_pin') is not True:
        raise ValueError('Genuine native configure context required')
    configure=json.loads(regular_bytes(root/'logs/python-configure.json'))
    configure_log=regular_bytes(root/'logs/python-configure.log')
    if configure.get('exit_code')!=0 or configure.get('cwd')!=str(objects) or configure.get('argv',[])[:4]!=['/usr/bin/sh','../work/Python-3.13.13/configure','--build=aarch64-unknown-linux-ohos','--host=aarch64-unknown-linux-ohos'] or configure.get('log_sha256')!=hashlib.sha256(configure_log).hexdigest() or binding.get('configure_log_sha256')!=configure.get('log_sha256'):
        raise ValueError('Native configure log owner/hash differs')
    build=json.loads(regular_bytes(root/'logs/python-build.json'));build_log=regular_bytes(root/'logs/python-build.log')
    argv=build.get('argv',[])
    if build.get('exit_code')!=0 or build.get('cwd')!=str(objects) or len(argv)!=2 or argv[0]!=selected_make(root,tools) or argv[1] not in ('-j1','-j2') or build.get('log_sha256')!=hashlib.sha256(build_log).hexdigest() or not build_log.endswith(b'exit_code=0\n'):
        raise ValueError('Completed own native build receipt required')
    values={'VERSION':'3.13','srcdir':'../work/Python-3.13.13','abs_srcdir':str(objects)+'/../work/Python-3.13.13','abs_builddir':str(objects),'SOABI':'cpython-313-aarch64-linux-ohos','EXT_SUFFIX':EXT_SUFFIX,'SHLIB_SUFFIX':'.so','MULTIARCH':'aarch64-linux-ohos','BUILD_GNU_TYPE':'aarch64-unknown-linux-ohos','HOST_GNU_TYPE':'aarch64-unknown-linux-ohos','ABIFLAGS':'','ABI_THREAD':''}
    def assignment(key):
        rows=re.findall(r'^'+re.escape(key)+r'[ \t]*([:+?]?=)[ \t]*([^\r\n]*?)[ \t]*$',make,re.M)
        if len(rows)!=1 or rows[0][0]!='=':raise ValueError('Ambiguous generated Makefile assignment: '+key)
        return rows[0][1]
    for key,value in values.items():
        if assignment(key)!=value:raise ValueError('Unsupported generated Makefile context: '+key)
    if tuple(assignment('MODSHARED_NAMES').split())!=SHARED_NAMES or assignment('SHAREDMODS').split()!=['Modules/'+n+'$(EXT_SUFFIX)' for n in SHARED_NAMES]:
        raise ValueError('Selected shared module table differs')
    for name in SHARED_NAMES:
        if assignment('MODULE_'+name.upper()+'_STATE')!='yes':raise ValueError('Selected shared module state differs')
    if regular_bytes(objects/'pybuilddir.txt').decode()!=BUILD_LIB:raise ValueError('Selected pybuilddir tag differs')
    prepared=json.loads(regular_bytes(root/'receipts/prepared-python.json'))
    invpath=HERE/'inventories/cpython-3.13.13.patched.json'
    if prepared.get('sources_lock_sha256')!=sha(HERE/'sources.lock.json') or prepared.get('sources',{}).get('cpython-3.13.13')!=sha(invpath):raise ValueError('Selected CPython prepared provenance differs')
    expected={r['path']:r for r in json.loads(invpath.read_text())}
    for name in ('Makefile.pre.in','Modules/makesetup'):
        for tree in ('sources','work'):
            data=regular_bytes(root/tree/'Python-3.13.13'/name)
            if len(data)!=expected[name]['size'] or hashlib.sha256(data).hexdigest()!=expected[name]['sha256']:raise ValueError('Upstream module alias producer differs')
    producer=regular_bytes(root/'sources/Python-3.13.13/Makefile.pre.in').decode()
    begin=producer.index('.PHONY: sharedmods\n');end=producer.index('\n# dependency on BUILDPYTHON',begin)
    if make.count(producer[begin:end])!=1:raise ValueError('Generated alias recipe body differs')
    for name in SHARED_NAMES:
        if (' -o Modules/'+name+EXT_SUFFIX+'\n').encode() not in build_log:raise ValueError('Own shared module link command missing')
    return objects,objects/BUILD_LIB


def check_module_alias(path,context):
    objects,lib=context;path=Path(path)
    if path.parent!=lib or path.name not in {n+EXT_SUFFIX for n in SHARED_NAMES}:raise ValueError('Unsupported generated module alias entry')
    if any(p.is_symlink() for p in (lib,*lib.parents)):raise ValueError('Generated module alias parent link refused')
    raw=os.readlink(path)
    if raw!='../../Modules/'+path.name:raise ValueError('Generated module alias raw target differs')
    target=objects/'Modules'/path.name
    header=regular_bytes(target,64)
    if path.resolve(strict=True)!=target or len(header)!=64 or header[:7]!=b'\x7fELF\x02\x01\x01' or struct.unpack_from('<HHI',header,16)!=(3,183,1) or struct.unpack_from('<H',header,52)[0]!=64 or target.stat().st_nlink!=1:
        raise ValueError('Existing regular AArch64 compiled shared module target required')
    # Header-only impostors cannot contain the tables advertised by the ELF header.
    phoff,shoff=struct.unpack_from('<QQ',header,32)
    phsize,phnum,shsize,shnum=struct.unpack_from('<HHHH',header,54)
    size=target.stat().st_size
    if phoff<64 or shoff<64 or phsize!=56 or shsize!=64 or not phnum or not shnum or phoff+phsize*phnum>size or shoff+shsize*shnum>size:
        raise ValueError('Compiled shared module ELF header/table extents differ')


def safe_outputs(root):
    root=Path(root);context=None
    for p in root.rglob('*'):
        mode=p.lstat().st_mode
        if stat.S_ISLNK(mode):
            top=p.relative_to(root).parts[0]
            if top=='cpython':
                if context is None:context=module_alias_context(root)
                check_module_alias(p,context)
                continue
            # Immutable sources still pass exact inventory verification separately.
            raw=os.readlink(p);target=p.resolve()
            if Path(raw).is_absolute() or not target.is_relative_to(root) or not target.exists() or not (target.is_file() or target.is_dir()):raise ValueError('Escaping/dangling owned output link')
            if top not in ('sources','work','runtime','numpy','deps'):raise ValueError('Link in owned tools/logs/receipts refused')
            if top!='sources' and any(part in ('tools','logs','receipts','markers','locks') for part in p.relative_to(root).parts[1:]):raise ValueError('Link in protected owned subtree refused')
        elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):raise ValueError('Owned special output refused')


class FailureReceipt:
    """Retain a real receipts directory before commands; never rescan unsafe outputs."""
    def __init__(self):self.root=None;self.fds=[];self.emitted=False
    def acquire(self,root):
        self.root=owned_marker(root);self.marker=regular_bytes(self.root/OWNER)
        (self.root/'receipts').mkdir(exist_ok=True)
        for path in (self.root,self.root/'receipts'):
            real_path(path);fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            self.fds.append(fd)
        self.identities=[(os.fstat(fd).st_dev,os.fstat(fd).st_ino) for fd in self.fds]
    def emit(self,primary,secondary):
        if len(self.fds)!=2 or self.emitted:raise ValueError('No unused retained failure receipt authority')
        owned_marker(self.root)
        if regular_bytes(self.root/OWNER)!=self.marker:raise ValueError('Failure owner marker replaced')
        for path,fd,identity in zip((self.root,self.root/'receipts'),self.fds,self.identities):
            real_path(path);current=path.lstat();held=os.fstat(fd)
            if not stat.S_ISDIR(current.st_mode) or (current.st_dev,current.st_ino)!=identity or (held.st_dev,held.st_ino)!=identity:raise ValueError('Retained failure receipts directory replaced')
        # A hostile receipts tree is never a writable authority, even if our
        # retained directory descriptor still refers to its previous inode.
        for p in (self.root/'receipts').rglob('*'):
            if not (stat.S_ISREG(p.lstat().st_mode) or stat.S_ISDIR(p.lstat().st_mode)):raise ValueError('Unsafe failure receipts tree refused')
        fd=os.open('full-failure.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.fds[1])
        with os.fdopen(fd,'w') as stream:
            current=os.fstat(stream.fileno())
            if not stat.S_ISREG(current.st_mode) or current.st_nlink!=1:raise ValueError('Regular dedicated failure leaf required')
            value={'schema':1,'status':'FAILED','inputs_lock_sha256':sha(HERE/'inputs.lock.json'),'primary_type':type(primary).__name__,'primary_message':str(primary),'primary_traceback':''.join(traceback.format_exception(primary)),'secondary_diagnostics':secondary,'native_full':'FAILED; no full success claim','outputs_retained':True}
            stream.write(json.dumps(value,indent=2,sort_keys=True,ensure_ascii=False)+'\n');stream.flush();os.fsync(stream.fileno())
        self.emitted=True
    def close(self):
        held=self.fds;self.fds=[];diagnostics=[]
        for fd in held:
            try:os.close(fd)
            except BaseException as error:diagnostics.append('failure authority cleanup: '+type(error).__name__+': '+str(error))
        return diagnostics


def owned(root,create=False):
    root=private_root(root);marker=root/OWNER
    expected={'schema':1,'owner':NAMESPACE,'inputs_lock_sha256':sha(HERE/'inputs.lock.json')}
    if root.exists():
        if not marker.is_file() or marker.is_symlink() or json.loads(marker.read_text())!=expected:raise ValueError('Unowned or differently sealed root refused')
        safe_outputs(root)
    elif create:
        root.mkdir(parents=True,exist_ok=False);root.chmod(0o700);dump(marker,expected)
    else:raise ValueError('Owned root missing')
    return root


def clean_env(tmp):
    env=dict(os.environ)
    for k in list(env):
        if k.startswith(('PYTHON','_PYTHON','ac_cv_','ax_cv_','CMAKE_','LIBFFI_','LIBSQLITE3_','PKG_CONFIG_','MESON_','GIT_','PERL')) or k in ('LD_LIBRARY_PATH','LD_PRELOAD','CC','CXX','CFLAGS','CXXFLAGS','CPPFLAGS','LDFLAGS','LIBS','CONFIG_SITE','CONFIG_SHELL','OHOS_PYTHON_UNVERSIONED_SONAME','NUMPY_PYTHON_PREFIX','MAKEFLAGS','MFLAGS','NINJAFLAGS','AR','RANLIB','NM','LD','STRIP','CPATH','C_INCLUDE_PATH','CPLUS_INCLUDE_PATH','LIBRARY_PATH','SDKROOT','COMPILER_PATH','INCLUDE','LIB','LIBPATH','ENV','BASH_ENV'): 
            env.pop(k,None)
    env.update(TMPDIR=str(tmp),PYTHONDONTWRITEBYTECODE='1',LC_ALL='C',CONFIG_SITE='/dev/null',CONFIG_SHELL='/usr/bin/sh')
    return env


class Runner:
    def __init__(self,root,env):self.root=Path(root);self.env=dict(env)
    def run(self,command,label,cwd=None,extra_env=None,timeout=None):
        command=list(map(str,command));env=dict(self.env);env.update(extra_env or {})
        log=self.root/'logs'/f'{label}.log';record=log.with_suffix('.json')
        if log.exists() or record.exists():raise ValueError('Refusing duplicate command receipt label: '+label)
        log.parent.mkdir(parents=True,exist_ok=True);start=time.time()
        with log.open('x') as stream:
            stream.write('$ '+shlex.join(command)+'\n');stream.flush()
            result=subprocess.run(command,cwd=cwd or self.root,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
            stream.write(result.stdout+'\nexit_code='+str(result.returncode)+'\n')
        dump(record,{'argv':command,'cwd':str(cwd or self.root),'exit_code':result.returncode,'seconds':time.time()-start,
                     'scoped_env':extra_env or {},'log_sha256':sha(log)})
        if result.returncode:raise RuntimeError(f'{label} failed exit={result.returncode}; inspect {log}')
        return result.stdout
