"""Persistent execution logs; immutable blocks share a balanced binary tree.

Appending copies at most one 128-entry block and a logarithmic tree path.
There is no parent-generation chain retaining obsolete tails. Entries follow
the state's existing append-only contract; callers must not edit their dicts.
"""
from collections.abc import MutableSequence, Sequence
import copy
from itertools import islice
import operator


LOG_CHUNK_SIZE = 128


def _chunk_at(node, depth, index):
    while depth:
        depth -= 1
        node = node[(index >> depth) & 1]
    return node


def _replace_chunk(node, depth, index, chunk):
    if not depth:
        return chunk
    left, right = node if node is not None else (None, None)
    half = 1 << (depth - 1)
    if index < half:
        return (_replace_chunk(left, depth - 1, index, chunk), right)
    return (left, _replace_chunk(right, depth - 1, index - half, chunk))


def _chunks(node, depth, reverse=False):
    if node is None:
        return
    if not depth:
        yield node
        return
    for child in reversed(node) if reverse else node:
        yield from _chunks(child, depth - 1, reverse)


def _different_chunks(node, before, depth, offset=0):
    if node is before or node is None:
        return
    if not depth:
        if node != before:
            yield offset, node
        return
    old_left, old_right = before if before is not None else (None, None)
    yield from _different_chunks(node[0], old_left, depth - 1, offset)
    yield from _different_chunks(node[1], old_right, depth - 1, offset + (1 << (depth - 1)))


class LogSequence(Sequence):
    __slots__ = ('_root', '_depth', '_length')

    def __init__(self, entries=()):
        if isinstance(entries, LogSequence):
            self._root, self._depth, self._length = entries._root, entries._depth, len(entries)
            return
        iterator = iter(entries)
        nodes = []
        length = 0
        while chunk := tuple(islice(iterator, LOG_CHUNK_SIZE)):
            nodes.append(chunk)
            length += len(chunk)
        depth = 0
        while len(nodes) > 1:
            nodes = [(nodes[i], nodes[i + 1] if i + 1 < len(nodes) else None)
                     for i in range(0, len(nodes), 2)]
            depth += 1
        self._root = nodes[0] if nodes else None
        self._depth, self._length = depth, length

    def __len__(self):
        return self._length

    def __iter__(self):
        for chunk in _chunks(self._root, self._depth):
            yield from chunk

    def __reversed__(self):
        for chunk in _chunks(self._root, self._depth, reverse=True):
            yield from reversed(chunk)

    def __getitem__(self, index):
        if isinstance(index, slice):
            # Summary tails and pagination only touch the requested blocks.
            return [self[i] for i in range(*index.indices(len(self)))]
        index = operator.index(index)
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError('log index out of range')
        return _chunk_at(self._root, self._depth, index // LOG_CHUNK_SIZE)[index % LOG_CHUNK_SIZE]

    def __eq__(self, other):
        if not isinstance(other, (Sequence, LogDraft)):
            return NotImplemented
        if len(self) != len(other):
            return False
        if isinstance(other, LogSequence):
            return not any(self.changed_chunks(other))
        return all(left == right for left, right in zip(self, other))

    def __deepcopy__(self, memo):
        # External callers still receive an ordinary, independently editable list.
        result = []
        memo[id(self)] = result
        result.extend(copy.deepcopy(item, memo) for item in self)
        return result

    def appended(self, entry):
        index = len(self) // LOG_CHUNK_SIZE
        tail_size = len(self) % LOG_CHUNK_SIZE
        chunk = _chunk_at(self._root, self._depth, index) if tail_size else ()
        root, depth = self._root, self._depth
        if index >= (1 << depth):
            root, depth = (root, None), depth + 1
        result = object.__new__(LogSequence)
        result._root = _replace_chunk(root, depth, index, (*chunk, entry))
        result._depth, result._length = depth, len(self) + 1
        return result

    def changed_chunks(self, before):
        """Compare immutable subtrees by identity, including coalesced generations.

        Unrelated/replaced sequences are compared by value for correctness.
        Removed chunks are handled by the store's existing truncation DELETE.
        """
        root, old_root = self._root, before._root
        depth = max(self._depth, before._depth)
        for _ in range(depth - self._depth):
            root = (root, None)
        for _ in range(depth - before._depth):
            old_root = (old_root, None)
        yield from _different_chunks(root, old_root, depth)


class LogDraft(MutableSequence):
    """Private callback view. Append shares blocks; arbitrary edits rebuild logs."""
    def __init__(self, entries):
        self._logs = LogSequence(entries)

    def __len__(self):
        return len(self._logs)

    def __getitem__(self, index):
        return self._logs[index]

    def __iter__(self):
        return iter(self._logs)

    def append(self, entry):
        self._logs = self._logs.appended(entry)

    def __setitem__(self, index, value):
        entries = list(self._logs)
        entries[index] = value
        self._logs = LogSequence(entries)

    def __delitem__(self, index):
        entries = list(self._logs)
        del entries[index]
        self._logs = LogSequence(entries)

    def insert(self, index, value):
        entries = list(self._logs)
        entries.insert(index, value)
        self._logs = LogSequence(entries)

    def freeze(self):
        return self._logs


def json_default(value):
    if isinstance(value, (LogSequence, LogDraft)):
        return list(value)
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')
