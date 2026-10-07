# SPDX-License-Identifier: GPL-2.0-or-later
"""Future actual signed embedding/dlopen and unsigned/tamper exec evidence."""
import sys
sys.dont_write_bytecode=True
import base64
import errno
import hashlib
import math
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import time
from source_guard import HERE,sha
from io_utils import clean_env,dump
from toolchain import sign_file


SIGNATURE_FIXTURE_TIMEOUT=10.0
SIGNATURE_FIXTURE_MARKER='OWN_NATIVE_SIGNATURE_TEST'


def _signature_error(error):
    return {'type':type(error).__name__,'errno':getattr(error,'errno',None),
            'strerror':getattr(error,'strerror',None),'text':str(error),
            'filename':os.fsdecode(error.filename) if getattr(error,'filename',None) is not None else None,
            'filename2':os.fsdecode(error.filename2) if getattr(error,'filename2',None) is not None else None}


def _signature_access(path,mode):
    if os.access in os.supports_effective_ids:return os.access(path,mode,effective_ids=True)
    # Real-ID checks are sufficient only when the child's effective IDs agree.
    return os.getuid()==os.geteuid() and os.getgid()==os.getegid() and os.access(path,mode)


def _signature_file(path):
    path=Path(path);row={'path':str(path)}
    try:
        status=path.lstat();mode=stat.S_IMODE(status.st_mode)
        row.update(currentmode=mode,currentmode_octal=oct(mode),regular=stat.S_ISREG(status.st_mode),
                   symlink=stat.S_ISLNK(status.st_mode),size=status.st_size,
                   readable=_signature_access(path,os.R_OK),executable=_signature_access(path,os.X_OK))
        row['sha256']=sha(path) if row['regular'] else None
    except OSError as error:row['error']=_signature_error(error)
    return row


def _signature_directories(paths):
    directories=set()
    for path in paths:directories.update(Path(path).parents)
    directories.add(Path(paths[0]))  # The explicit subprocess cwd itself.
    rows=[]
    for path in sorted(directories,key=str):
        row={'path':str(path)}
        try:
            status=path.lstat();mode=stat.S_IMODE(status.st_mode)
            row.update(currentmode=mode,currentmode_octal=oct(mode),directory=stat.S_ISDIR(status.st_mode),
                       symlink=stat.S_ISLNK(status.st_mode),traversable=_signature_access(path,os.X_OK))
        except OSError as error:row['error']=_signature_error(error)
        rows.append(row)
    return rows


def _signature_stream(data):
    # Production always captures bytes; retain mock/error text as UTF-8 explicitly.
    origin='bytes'
    if data is None:data=b''
    elif isinstance(data,str):data=data.encode('utf-8',errors='surrogatepass');origin='text-encoded-utf8'
    return {'bytes_base64':base64.b64encode(data).decode('ascii'),'size':len(data),
            'sha256':hashlib.sha256(data).hexdigest(),'text':data.decode('utf-8',errors='replace'),
            'text_encoding':'utf-8','text_errors':'replace','capture_type':origin}


def _signature_context_ready(record):
    fixture=record['fixture'];source=record['source']
    return (fixture==record['fixture_after'] and source==record['source_after'] and
            record['directories']==record['directories_after'] and
            fixture.get('regular') is True and fixture.get('readable') is True and
            fixture.get('executable') is True and bool(fixture.get('currentmode',0)&0o111) and
            isinstance(fixture.get('sha256'),str) and source.get('regular') is True and
            source.get('readable') is True and bool(source.get('currentmode',0)&0o444) and
            isinstance(source.get('sha256'),str) and
            all(row.get('directory') is True and row.get('traversable') is True and
                bool(row.get('currentmode',0)&0o111) for row in record['directories']))


def _signature_control_ready(record,control):
    if not control:return False
    return (control.get('case')=='signed-positive' and control.get('result')=='PASS' and
            control.get('classification')=='signed-positive-executed' and
            control.get('returncode')==0 and control.get('signal') is None and
            control.get('error') is None and not control.get('timed_out') and
            control['stdout']['text'].splitlines()==[SIGNATURE_FIXTURE_MARKER] and
            _signature_context_ready(control) and control['source']==record['source'] and
            control['cwd']==record['cwd'] and control['environment_sha256']==record['environment_sha256'] and
            Path(control['fixture']['path']).parent==Path(record['fixture']['path']).parent and
            _signature_file(control['fixture']['path'])==control['fixture_after'] and
            Path(control['receipt_path']).is_file() and
            sha(control['receipt_path'])==record['signed_positive_control']['receipt_sha256'])


def signature_execution_evidence(path,*,case,source,cwd,tmp_dir,receipt_path,
                                 timeout=SIGNATURE_FIXTURE_TIMEOUT,signed_control=None):
    """Future runtime caller only: collect and persist an attempt before validation.

    EPERM/EACCES can establish exec-permission rejection with sufficient controls.
    Neither this classification nor a terminating signal proves signature policy.
    Source fixtures must mock subprocess.run; importing this module executes nothing.
    """
    if not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:
        raise ValueError('Signature fixture requires an explicit finite positive timeout')
    if case not in ('signed-positive','unsigned','tampered-after-sign'):
        raise ValueError('Unknown own signature fixture case')
    path=Path(path).absolute();source=Path(source).absolute();cwd=Path(cwd).absolute()
    receipt_path=Path(receipt_path).absolute()
    if receipt_path.exists():raise ValueError('Refusing duplicate signature evidence receipt')
    env=clean_env(tmp_dir)
    env_digest=hashlib.sha256()
    for key,value in sorted(env.items()):
        env_digest.update(os.fsencode(key)+b'\0'+os.fsencode(value)+b'\0')
    record={'schema':1,'case':case,'evidence_kind':'runtime-subprocess-attempt',
            'argv':[str(path)],'cwd':str(cwd),'timeout_seconds':float(timeout),
            'receipt_path':str(receipt_path),'environment_sha256':env_digest.hexdigest(),
            'fixture':_signature_file(path),'source':_signature_file(source),
            'directories':_signature_directories([cwd,path,source]),
            'returncode':None,'signal':None,'signal_name':None,'errno':None,'error_filename':None,
            'error':None,'timed_out':False,'signature_policy_proven':False,'kernel_rejected':False,
            'signed_positive_control':None}
    if signed_control is not None:
        control_path=Path(signed_control['receipt_path'])
        record['signed_positive_control']={'receipt_path':str(control_path),
            'receipt_sha256':sha(control_path) if control_path.is_file() else None,'evidence':signed_control}
    stdout=b'';stderr=b'';start=time.monotonic_ns()
    try:
        result=subprocess.run(record['argv'],cwd=str(cwd),env=env,capture_output=True,
                              text=False,timeout=float(timeout),check=False)
    except subprocess.TimeoutExpired as error:
        stdout=error.output;stderr=error.stderr
        record.update(timed_out=True,error=_signature_error(error))
    except OSError as error:
        stdout=getattr(error,'stdout',b'');stderr=getattr(error,'stderr',b'')
        record.update(errno=error.errno,error_filename=os.fsdecode(error.filename) if error.filename is not None else None,
                      error=_signature_error(error))
    else:
        stdout=result.stdout;stderr=result.stderr;record['returncode']=result.returncode
        if result.returncode<0:
            record['signal']=-result.returncode
            try:record['signal_name']=signal.Signals(-result.returncode).name
            except ValueError:pass
    finally:
        end=time.monotonic_ns()
        record['timing']={'clock':'monotonic_ns','started_ns':start,'finished_ns':end,
                          'elapsed_seconds':(end-start)/1_000_000_000}
    record.update(stdout=_signature_stream(stdout),stderr=_signature_stream(stderr),
                  fixture_after=_signature_file(path),source_after=_signature_file(source),
                  directories_after=_signature_directories([cwd,path,source]))
    context_ready=_signature_context_ready(record)
    record['context_sufficient']=context_ready
    if record['timed_out']:
        record.update(result='NOTREADY',classification='timeout')
    elif record['error'] is not None:
        if record['errno'] in (errno.EACCES,errno.EPERM) and case!='signed-positive':
            control_ready=_signature_control_ready(record,signed_control)
            record['signed_positive_control_sufficient']=control_ready
            if context_ready and control_ready and record['error_filename']==str(path):
                record.update(result='PASS',classification='exec-permission-rejection')
            else:record.update(result='NOTREADY',classification='permission-error-insufficient-evidence')
        else:record.update(result='NOTREADY',classification='launch-error')
    elif record['signal'] is not None:
        record.update(result='NOTREADY',classification='unexplained-signal')
    elif record['returncode']!=0:
        record.update(result='FAIL',classification='nonzero-exit')
    elif case!='signed-positive':
        record.update(result='FAIL',classification='forbidden-fixture-executed')
    elif not context_ready or record['stdout']['text'].splitlines()!=[SIGNATURE_FIXTURE_MARKER]:
        record.update(result='NOTREADY',classification='signed-positive-insufficient-evidence')
    else:record.update(result='PASS',classification='signed-positive-executed')
    dump(receipt_path,record)
    return record


def require_signature_evidence(record):
    if record['result']!='PASS':
        raise RuntimeError('Signature fixture '+record['case']+' '+record['result']+': '+
                           record['classification']+'; inspect '+record['receipt_path'])


def run(args,root,runner):
    root=Path(root);work=root/'embedding';work.mkdir(exist_ok=False);runtime=root/'runtime';helper=HERE/'python/helpers'
    prefix=[args.python,helper/'sign_compiler.py',args.cc,'--target=aarch64-unknown-linux-ohos','--sysroot='+str(Path(args.sdk_root)/'sysroot'),
       '-resource-dir='+str(args.resource_dir),'--ld-path='+str(args.lld),'-Wl,--threads=1','--']
    env={'OHOS_BINARY_SIGN_TOOL':str(args.signer),'OHOS_LLVM_READELF':str(args.readelf)}
    common=['-O2','-I'+str(runtime/'include/python3.13'),'-L'+str(runtime/'lib'),'-lpython3.13','-Wl,-rpath,'+str(runtime/'lib')]
    # Diagnostic probe RPATH is permitted ONLY inside own work, never runtime/HAP.
    embed=work/'embed';consumer=work/'consumer.so';dlopen=work/'dlopen'
    runner.run([*prefix,helper/'embed-hap-smoke.c',*common,'-o',embed],'embedding-link',extra_env=env)
    runner.run([embed,runtime],'embedding-native-run')
    runner.run([*prefix,'-shared','-fPIC',helper/'dlopen-consumer.c',*common,'-o',consumer],'dlopen-consumer-link',extra_env=env)
    runner.run([*prefix,helper/'dlopen-smoke.c','-ldl','-o',dlopen],'dlopen-driver-link',extra_env=env)
    result=runner.run([dlopen,runtime,consumer],'dlopen-native-run',extra_env={'LD_LIBRARY_PATH':str(runtime/'lib')})
    if 'one libpython3.13.so' not in result:raise ValueError('Actual unique shared PyRuntime receipt missing')
    flat=root/'hap-native/libs/arm64-v8a';raw=root/'hap-resources/resources/rawfile/python'
    runner.run([dlopen,raw,consumer,flat/'libpython3.13.so',flat],'dlopen-flat-native-run',extra_env={'LD_LIBRARY_PATH':str(flat)})
    source=work/'signature.c';source.write_text('#include <stdio.h>\nint main(void){puts("OWN_NATIVE_SIGNATURE_TEST");return 0;}\n')
    unsigned=work/'unsigned';signed=work/'signed';tampered=work/'tampered'
    direct=[args.cc,'--target=aarch64-unknown-linux-ohos','--sysroot='+str(Path(args.sdk_root)/'sysroot'),'-resource-dir='+str(args.resource_dir),
       '--ld-path='+str(args.lld),'-Wl,--threads=1',source,'-o',unsigned]
    runner.run(direct,'kernel-negative-original-unsigned-link')
    unsigned.chmod(0o755);shutil.copyfile(unsigned,signed);sign_file(args,signed)
    positive=signature_execution_evidence(signed,case='signed-positive',source=source,cwd=work,
        tmp_dir=args.tmp_dir,receipt_path=root/'receipts/signature-signed-positive.json',
        timeout=SIGNATURE_FIXTURE_TIMEOUT)
    require_signature_evidence(positive)
    shutil.copyfile(signed,tampered);data=tampered.read_bytes();marker=b'OWN_NATIVE_SIGNATURE_TEST'
    offset=data.find(marker)
    if offset<0 or data.find(marker,offset+1)>=0:raise ValueError('Unique protected signature fixture marker unavailable')
    data=data[:offset]+b'X'+data[offset+1:];tampered.write_bytes(data);tampered.chmod(0o755)
    negatives=[]
    for name,path in [('unsigned',unsigned),('tampered-after-sign',tampered)]:
        negatives.append(signature_execution_evidence(path,case=name,source=source,cwd=work,
            tmp_dir=args.tmp_dir,receipt_path=root/('receipts/signature-'+name+'.json'),
            timeout=SIGNATURE_FIXTURE_TIMEOUT,signed_control=positive))
    dump(root/'receipts/signature-execution-summary.json',{'schema':1,'signed_positive':positive,
        'negatives':negatives,'signature_policy_proven':False,
        'result':'PASS exec-permission rejection evidence' if all(p['result']=='PASS' for p in negatives) else 'NOTREADY'})
    for negative in negatives:require_signature_evidence(negative)
    # Native runtime relocation tests use the same signed bytes in a private UTF/space
    # prefix; source checkout migration is separately covered by source-only receipts.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='python-native-runtime-迁移 with spaces-',dir=args.tmp_dir) as td:
        moved=Path(td)/'Python runtime 例子';shutil.copytree(runtime,moved,symlinks=True)
        runner.run([moved/'bin/python3.13','-I','-c','import sys,sysconfig,numpy,requests,certifi;assert sys.version_info[:3]==(3,13,13);assert sysconfig.get_config_var("INSTSONAME")=="libpython3.13.so";assert numpy.__version__=="2.3.4";print("actual migrated native Python+NumPy+pure8 imports PASS")'],'native-runtime-Unicode-space')
        runner.run([dlopen,moved,consumer],'native-runtime-Unicode-space-dlopen',extra_env={'LD_LIBRARY_PATH':str(moved/'lib')})
        migration={'actual':True,'same_signed_runtime_sha256':sha(moved/'lib/libpython3.13.so'),'source_runtime_sha256':sha(runtime/'lib/libpython3.13.so'),'temporary_cleaned':True}
        if migration['same_signed_runtime_sha256']!=migration['source_runtime_sha256']:raise ValueError('Runtime migration signed bytes differ')
    receipt={'schema':2,'result':'PASS actual terminal signed embedding/global+local dlopen/single shared runtime/native migration/exec-permission negatives',
        'source_selected_SONAME':'libpython3.13.so','runtime_ELF_unchanged':sha(runtime/'lib/libpython3.13.so'),
        'signed_positive':positive,'negatives':negatives,'signature_policy_proven':False,'migration':migration,
        'artifacts':[{'path':str(p.relative_to(root)),'sha256':sha(p)} for p in (embed,consumer,dlopen,signed)],'actual_installed_HAP':'NOTRUN','Blender_postlink':'NOTRUN'}
    dump(root/'receipts/embedding-dlopen.json',receipt);return receipt
