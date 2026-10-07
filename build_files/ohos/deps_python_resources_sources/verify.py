#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify registry and optional owned complete-source cache/pure resources."""
import runpy
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.argv.insert(1, 'verify')
runpy.run_path(str(Path(__file__).with_name('resources.py')), run_name='__main__')
