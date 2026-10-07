# SPDX-License-Identifier: GPL-2.0-or-later
"""Resolve actual native shell/link arguments with their working directory."""
from pathlib import Path
import shlex

TRACKED = {'tbb', 'tbbmalloc', 'osdCPU', 'osdGPU', 'gmp', 'gmpxx', 'manifold', 'shaderc_combined'}
OPERATORS = {'&&', '||', ';', '|', '&'}
NATIVE_RUNTIME = {'c', 'm', 'dl', 'pthread', 'c++', 'c++abi', 'c++experimental', 'atomic', 'unwind'}


def words(text):
    # pkgconf/Ninja may shell-escape each UTF8 byte. Unescape on a byte carrier
    # first; decoding before removing backslashes corrupts Unicode path bytes.
    raw = text if isinstance(text, bytes) else text.encode('utf-8', errors='surrogateescape')
    lexer = shlex.shlex(raw.decode('latin-1'), posix=True, punctuation_chars=';&|')
    lexer.whitespace_split = True
    lexer.commenters = ''
    return [token.encode('latin-1').decode('utf-8') for token in lexer]


def absolute(token, cwd):
    file = Path(token)
    return (file if file.is_absolute() else cwd / file).resolve()


def expand(tokens, cwd, depth=0):
    if depth > 8:
        raise ValueError('Response-file nesting exceeds signing/parser contract')
    result = []
    for token in tokens:
        if token.startswith('@'):
            file = absolute(token[1:], cwd)
            if not file.is_file():
                raise ValueError('Actual built link response file missing: ' + str(file))
            result.extend(expand(words(file.read_bytes()), cwd, depth + 1))
        elif token.startswith('-Wl,'):
            result.extend(token[4:].split(','))
        else:
            result.append(token)
    return result


def inspect(commands, build, compiler_search_dirs):
    """Parse every command, including wrappers; preserve linker -L precedence."""
    archive_paths, rows = set(), []
    for line in commands.splitlines():
        cwd = Path(build).resolve()
        syntax_line = line.decode('latin-1') if isinstance(line, bytes) else line
        if any(marker in syntax_line for marker in ['`', '$(', '${']):
            raise ValueError('Unobservable native shell expansion in link plan')
        tokens = words(line)
        if any(token in {'if', 'then', 'else', 'fi', 'for', 'while', 'do', 'done'} for token in tokens):
            raise ValueError('Unsupported native shell control syntax; inspect real build log')
        # Native Ninja shell commands normally use cd/&&; honor actual changes.
        chunks, chunk = [], []
        for token in tokens + [';']:
            if token in OPERATORS:
                if chunk:
                    if chunk[0] == 'cd' and len(chunk) == 2:
                        cwd = absolute(chunk[1], cwd)
                    elif len(chunk) >= 4 and chunk[1:3] == ['-E', 'chdir']:
                        chunks.append((absolute(chunk[3], cwd), chunk[4:]))
                    else:
                        chunks.append((cwd, chunk))
                chunk = []
            else:
                chunk.append(token)
        for current, chunk in chunks:
            args = expand(chunk, current)
            directories = [Path(p).resolve() for p in compiler_search_dirs]
            requests, direct = [], []
            index = 0
            while index < len(args):
                token = args[index]
                if token in ('-L', '-l'):
                    if index + 1 == len(args):
                        raise ValueError('Incomplete actual native linker option')
                    value = args[index + 1]
                    index += 1
                    if token == '-L':
                        directories.append(absolute(value, current))
                    else:
                        requests.append(value)
                elif token.startswith('-L') and len(token) > 2:
                    directories.append(absolute(token[2:], current))
                elif token.startswith('-l') and len(token) > 2 and not token.startswith('-load'):
                    requests.append(token[2:])
                elif token.endswith('.a') and not token.startswith('-'):
                    file = absolute(token, current)
                    if not file.is_file():
                        raise ValueError('Actual explicit archive missing: ' + str(file))
                    archive_paths.add(file)
                    direct.append(str(file))
                index += 1
            selected = []
            for name in requests:
                leaves = [name[1:]] if name.startswith(':') else ['lib' + name + '.so', 'lib' + name + '.a']
                choice = next((directory / leaf for directory in directories for leaf in leaves if (directory / leaf).is_file()), None)
                if choice is None:
                    if name.lstrip(':') in TRACKED or name.lstrip(':') in {'lib' + n + '.a' for n in TRACKED}:
                        raise ValueError('Tracked bare -l input has no explicit actual SDK/-L resolution: ' + name)
                    if name not in NATIVE_RUNTIME:
                        raise ValueError('Unresolved non-SDK native library option: -l' + name)
                    selected.append({'option': '-l' + name, 'resolution': 'declared native SDK runtime only; ELF NEEDED must be libc.so'})
                    continue
                real = choice.resolve()
                if name in NATIVE_RUNTIME and not any(real.is_relative_to(Path(p).resolve()) for p in compiler_search_dirs):
                    raise ValueError('Native runtime option selected outside declared SDK: ' + str(real))
                if real.suffix == '.a':
                    archive_paths.add(real)
                selected.append({'option': '-l' + name, 'selected': str(real), 'searched': [str(p) for p in directories]})
            if direct or requests:
                rows.append({'cwd': str(current), 'direct_archives': direct, 'library_options': selected})
    return {'archives': sorted(map(str, archive_paths)), 'commands': rows,
            'parser': 'UTF8 POSIX shell words; actual Ninja cwd/cd/chdir; built response files; explicit/relative archives and ordered -L/-l'}
