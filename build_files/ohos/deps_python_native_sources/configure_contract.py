# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded validation of real generated Makefile fields and native configure output.
This is a contract parser for six fields, not a general GNU Make interpreter.
It never invokes configure, make, a generator, a shell or a target executable.
"""
import re
from pathlib import Path

FIELDS=('VERSION','ABIFLAGS','LDVERSION','LDLIBRARY','INSTSONAME','PYTHON_FOR_REGEN')
ASSIGN=re.compile(r'([A-Za-z_][A-Za-z_0-9]*)\s*(::=|:=|\?=|\+=|=)\s*(.*)')
REFERENCE=re.compile(r'\$\(([A-Za-z_][A-Za-z_0-9]*)\)|\$\{([A-Za-z_][A-Za-z_0-9]*)\}')


def generated_fields(text):
    definitions={};condition=[];in_define=False
    def expand(value,scope,trail=()):
        def substitute(match):
            name=match.group(1) or match.group(2)
            if name not in ('VERSION','ABIFLAGS','LDVERSION') or name not in scope:raise ValueError('Unknown or forward immediate Make reference: '+name)
            if name in trail:raise ValueError('Cyclic generated Make expression')
            item=scope[name]
            return item['value'] if item['operator']==':=' else expand(item['raw'],scope,trail+(name,))
        result=REFERENCE.sub(substitute,value)
        if any(c in result for c in ('$', '@', '#', '\\','\x00','\n','\r')):raise ValueError('Unsupported generated Make expression')
        return result
    for number,line in enumerate(text.splitlines(),1):
        if line.startswith('\t') or not line.strip() or line.lstrip().startswith('#'):continue
        token=line.strip().split(None,1)[0]
        if token in ('ifdef','ifndef','ifeq','ifneq'):condition.append(token);continue
        if token=='endif':
            if not condition:raise ValueError('Unbalanced Make conditional')
            condition.pop();continue
        if token=='define':in_define=True;continue
        if token=='endef':in_define=False;continue
        match=ASSIGN.fullmatch(line.strip())
        if not match:
            # Export/override/define or unknown operators cannot secretly redefine a
            # contract field; ordinary unrelated rules/assignments are outside scope.
            if re.match(r'(?:(?:export|override|private)\s+)?(?:'+ '|'.join(FIELDS)+r')\b',line.strip()):raise ValueError('Unsupported contract field assignment')
            continue
        name,operator,raw=match.groups()
        if name not in FIELDS:continue
        if name in definitions:raise ValueError('Duplicate/conflicting generated Make field: '+name)
        if condition or in_define:raise ValueError('Conditional/defined contract assignment is ambiguous')
        if operator=='::=':raise ValueError('Unsupported immediate assignment operator')
        if operator=='+=':raise ValueError('Append inherits an undefined/environment base; cannot establish contract field')
        if operator=='?=' and name!='PYTHON_FOR_REGEN':raise ValueError('Conditional ABI field can inherit undeclared environment')
        if name=='PYTHON_FOR_REGEN' and any(c in raw for c in ('$', '@', '#', '\\','\x00','\n','\r')):raise ValueError('Generator must be a literal generated path')
        item={'operator':operator,'raw':raw,'line':number}
        if operator==':=':item['value']=expand(raw,definitions,(name,))
        definitions[name]=item
    if condition or in_define:raise ValueError('Unclosed Make conditional/definition')
    if set(definitions)!=set(FIELDS):raise ValueError('Missing generated native Make contract fields')
    values={name:item['value'] if item['operator']==':=' else expand(item['raw'],definitions,(name,)) for name,item in definitions.items()}
    return values,definitions


def validate(config_output,makefile,regen_python,environment):
    expected=str(regen_python)
    if not Path(expected).is_absolute() or '..' in Path(expected).parts or any(ord(c)<32 or ord(c)==127 for c in expected):raise ValueError('Canonical absolute explicit generator path required')
    lines=[line.strip() for line in config_output.splitlines()]
    if lines.count('checking whether we are cross compiling... no')!=1 or any(line.startswith('checking whether we are cross compiling... ') and line!='checking whether we are cross compiling... no' for line in lines):raise ValueError('Real native cross_compiling=no evidence missing/conflicting')
    version='checking Python for regen version... Python 3.13.13'
    if lines.count(version)!=1 or any(line.startswith('checking Python for regen version... ') and line!=version for line in lines):raise ValueError('Actual configure native3.13.13 generator version evidence missing/conflicting')
    selections=[line for line in lines if re.fullmatch(r'checking for [A-Za-z_0-9.+-]+\.\.\. '+re.escape(expected),line)]
    if len(selections)!=1:raise ValueError('Real AC_CHECK_PROGS explicit generator selection evidence missing')
    values,definitions=generated_fields(makefile)
    if environment.get('PYTHON_FOR_REGEN')!=expected or values['PYTHON_FOR_REGEN']!=expected:raise ValueError('Actual generated Makefile AND make environment must bind exact pinned generator')
    if values['VERSION']!='3.13' or values['ABIFLAGS']!='' or values['LDVERSION']!='3.13':raise ValueError('Unexpected native Python/regular-GIL ABI')
    if values['INSTSONAME']!='libpython3.13.so' or values['LDLIBRARY']!='libpython3.13.so':raise ValueError('Source-selected genuine unversioned Python SONAME required before linking')
    return {'native_cross_compiling':'no','regen_version':'Python 3.13.13','actual_selection_line':selections[0],
            'generated_values':values,'assignment_operators':{name:item['operator'] for name,item in definitions.items()},
            'generator_environment_and_Makefile_same_absolute_pin':True,'unknown_expressions_duplicates_and_append_base_rejected':True}
