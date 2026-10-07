# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-download response guards; no Requests redirect/security code is copied."""
from __future__ import annotations

import time
import zlib
from collections.abc import Callable, Iterator

import requests


class RedirectBudgetExceeded(requests.RequestException):
    """Redirect wire or decoded aggregate exceeds max_redirect_bytes."""


def decoded_chunks(raw, encoding: str, chunk_size: int, check: Callable[[], None],
                   on_wire: Callable[[bytes], None], *, multiple_members: bool = False,
                   require_eof: bool = True) -> Iterator[bytes]:
    """Bound raw reads and every zlib output to chunk_size (never zlib max_length=0).

    Final assets retain Blender's first-gzip-member policy: trailing bytes and
    subsequent members are ignored but still counted on the wire. Redirect gzip
    follows Requests/urllib3's multiple-member and trailing-garbage policy.
    Complete streams need no flush: drain decompress(b'', positive max_length)
    before testing eof, avoiding flush(length)'s *initial*, non-limiting buffer.
    """
    if chunk_size <= 0:
        raise ValueError('HTTP chunk size must be positive')
    if encoding not in ('', 'gzip', 'deflate'):
        raise requests.exceptions.ContentDecodingError(
            f'Unsupported bounded redirect content encoding: {encoding!r}')
    decoder = (zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == 'gzip' else
               zlib.decompressobj() if encoding == 'deflate' else None)
    # Preserve Requests' wrapped-first/raw-deflate fallback before any output.
    # This compressed prefix is bounded by the already-charged redirect wire
    # aggregate. Only the decoder replays it; never re-read the socket, yield
    # duplicate output or reset/charge the transfer counters again.
    deflate_prefix = bytearray()
    deflate_first_try = encoding == 'deflate'
    completed_member = False
    swallow = False
    while True:
        check()
        wire = raw.read1(chunk_size, decode_content=False)
        check()
        if not wire:
            break
        on_wire(wire)
        if deflate_first_try:
            deflate_prefix.extend(wire)
        if decoder is None:
            yield wire
            continue
        pending = wire
        replay = None
        while not swallow:
            check()
            if decoder.eof:
                completed_member = True
                if not multiple_members or encoding != 'gzip':
                    break
                if not pending:
                    break
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            try:
                output = decoder.decompress(pending, chunk_size)
            except zlib.error:
                if deflate_first_try:
                    deflate_first_try = False
                    decoder = zlib.decompressobj(-zlib.MAX_WBITS)
                    saved = bytes(deflate_prefix)
                    deflate_prefix.clear()
                    replay = (saved[offset:offset + chunk_size] for offset in range(0, len(saved), chunk_size))
                    pending = next(replay, b'')
                    continue
                if multiple_members and completed_member:
                    swallow = True
                    break
                raise
            pending = decoder.unused_data if decoder.eof else decoder.unconsumed_tail
            if output:
                if deflate_first_try:
                    deflate_first_try = False
                    deflate_prefix.clear()
                yield output
            if not pending and len(output) < chunk_size:
                pending = next(replay, b'') if replay is not None else b''
                if not pending:
                    break
            # Even without unconsumed input, zlib may have pending output.
    if decoder is not None and not swallow:
        while not decoder.eof:
            check()
            output = decoder.decompress(b'', chunk_size)
            if not output:
                break
            yield output
        if require_eof and not decoder.eof:
            raise requests.exceptions.ContentDecodingError('Incomplete gzip response')


class TransferBudget:
    """A closure-owned context; never stored on a shared Session or adapter."""

    def __init__(self, check: Callable[[], None], chunk_size: int,
                 redirect_bytes: int, max_redirects: int, total_timeout: float):
        if chunk_size <= 0 or redirect_bytes <= 0 or max_redirects < 0 or total_timeout < 0:
            raise ValueError('Invalid HTTP transfer budget')
        self.periodic_check = check
        self.chunk_size = chunk_size
        self.redirect_bytes = redirect_bytes
        self.max_redirects = max_redirects
        self.deadline = time.monotonic() + total_timeout if total_timeout else None
        self.redirect_count = 0
        self.wire_bytes = 0
        self.decoded_bytes = 0
        self.responses = []

    def check(self):
        self.periodic_check()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise requests.Timeout('HTTP download total deadline exceeded')

    def close(self):
        for response in self.responses:
            response.close()
        self.responses.clear()

    def response_hook(self, response, **_kwargs):
        # Public Requests hooks run before resolve_redirects consumes .content.
        self.responses.append(response)
        try:
            self.check()
            if response.is_redirect:
                self.redirect_count += 1
                if self.redirect_count > self.max_redirects:
                    raise requests.TooManyRedirects(
                        f'Exceeded {self.max_redirects} redirects.', response=response)
                response.raw = _RedirectRaw(response.raw, response.headers, self)
            return response
        except BaseException:
            response.close()
            raise


class _RedirectRaw:
    """Preserve urllib3 metadata/cookie plumbing; bound Requests' eager body read.

    stream() is the Requests .content entry. read() also guards its fallback
    drain. Failed reads remain failed, including RuntimeError-based cancellation
    caught by Requests' resolve_redirects. No fallback can resume or allocate.
    """

    def __init__(self, raw, headers, budget):
        self._raw = raw
        self._headers = headers
        self._budget = budget
        self._failure = None
        self._started = False

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def _count_wire(self, data):
        actual = self._budget.wire_bytes + len(data)
        if actual > self._budget.redirect_bytes:
            raise RedirectBudgetExceeded('redirect wire aggregate', self._budget.redirect_bytes, actual)
        self._budget.wire_bytes = actual

    def stream(self, amt=None, decode_content=None):
        if self._failure is not None:
            raise self._failure
        if self._started:
            return
        self._started = True
        try:
            size = min(amt or self._budget.chunk_size, self._budget.chunk_size)
            if size <= 0:
                raise ValueError('redirect chunk size must be positive')
            encoding = (self._headers.get('Content-Encoding') or '').strip().lower() if decode_content else ''
            for data in decoded_chunks(self._raw, encoding, size, self._budget.check,
                                       self._count_wire, multiple_members=True, require_eof=False):
                self._budget.check()
                actual = self._budget.decoded_bytes + len(data)
                if actual > self._budget.redirect_bytes:
                    raise RedirectBudgetExceeded('redirect decoded aggregate', self._budget.redirect_bytes, actual)
                self._budget.decoded_bytes = actual
                yield data
        except BaseException as error:
            self._failure = error
            self._raw.close()
            raise

    def read(self, amt=None, decode_content=None, **_kwargs):
        # Requests fallback uses read() without amt; aggregate is still bounded.
        return b''.join(self.stream(amt, decode_content))
