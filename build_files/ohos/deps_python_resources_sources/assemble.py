#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Assemble only the eight pure distributions into an independently owned root."""
import runpy
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.argv.insert(1, 'assemble')
runpy.run_path(str(Path(__file__).with_name('resources.py')), run_name='__main__')
