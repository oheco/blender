# SPDX-License-Identifier: GPL-2.0-or-later
"""Inert CMake command lexer copied from frozen editor R5 SOURCE consumer.

Original: revision5-native-facts-candidate config_interfaces.commands:99-164.
No evaluation/import of that consumer and no capture protocol admission.
"""
import re
from capture_io import require as need


def commands(text):
    """Tokenize commands including quoted/bracket arguments; no substring binding."""
    need(len(text) <= 4_000_000 and '\x00' not in text, 'Oversized/invalid CMake metadata')
    result, pos, size = [], 0, len(text)
    def space():
        nonlocal pos
        while pos < size:
            if text[pos].isspace():
                pos += 1
            elif text[pos] == '#':
                match = re.match(r'#\[(=*)\[', text[pos:])
                if match:
                    end = text.find(']' + match[1] + ']', pos + len(match[0]))
                    need(end >= 0, 'Unclosed bracket comment')
                    pos = end + len(match[1]) + 2
                else:
                    end = text.find('\n', pos)
                    pos = size if end < 0 else end + 1
            else:
                break
    while True:
        space()
        if pos == size:
            return result
        line = text.count('\n', 0, pos) + 1
        match = re.match(r'[A-Za-z_][A-Za-z0-9_]*', text[pos:])
        need(match is not None, 'Expected CMake command at line ' + str(line))
        name = match[0].lower(); pos += len(match[0]); space()
        need(pos < size and text[pos] == '(', 'Missing CMake command opening delimiter')
        pos += 1; args = []; depth = 1
        while depth:
            space(); need(pos < size, 'Unclosed CMake command')
            if text[pos] == ')':
                depth -= 1; pos += 1
                if depth: args.append((')', False))
                continue
            if text[pos] == '(':
                depth += 1; pos += 1; args.append(('(', False)); continue
            bracket = re.match(r'\[(=*)\[', text[pos:])
            if bracket:
                start = pos + len(bracket[0]); end = text.find(']' + bracket[1] + ']', start)
                need(end >= 0, 'Unclosed bracket argument')
                args.append((text[start:end], 'bracket')); pos = end + len(bracket[1]) + 2; continue
            quoted = text[pos] == '"'
            if quoted: pos += 1
            value = []
            while pos < size:
                char = text[pos]
                if quoted and char == '"':
                    pos += 1; break
                if not quoted and (char.isspace() or char in '()'):
                    break
                if char == '\\':
                    pos += 1; need(pos < size, 'Incomplete CMake escape')
                    char = text[pos]
                    if char == '\n': pos += 1; continue
                    need(not char.isalnum() or char in ('n', 'r', 't'), 'Unsupported/invalid CMake character escape')
                    value.append({'n': '\n', 'r': '\r', 't': '\t', ';': '\x1e', '$': '\x1f'}.get(char, char))
                else:
                    value.append(char)
                pos += 1
            else:
                need(not quoted, 'Unclosed quoted CMake argument')
            args.append((''.join(value), quoted))
        result.append((name, args, line))
        need(len(result) <= 40_000, 'CMake command bound exceeded')
