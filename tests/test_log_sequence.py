import copy
import json
import random
import unittest
from unittest.mock import patch

import log_sequence as logs


class LogSequenceTests(unittest.TestCase):
    def test_append_boundaries_share_prefix_and_preserve_old_generations(self):
        sequence = logs.LogSequence()
        expected = []
        saved = []
        for i in range(2051):
            if i in (0, 127, 128, 129, 255, 256, 511, 512, 1023, 1024, 2048):
                saved.append((sequence, list(expected)))
            entry = {'message': str(i)}
            sequence = sequence.appended(entry)
            expected.append(entry)
        self.assertEqual(sequence, expected)
        self.assertEqual(expected, sequence)
        for previous, entries in saved:
            self.assertEqual(previous, entries)
            self.assertEqual(list(reversed(previous)), list(reversed(entries)))
        self.assertIs(logs._chunk_at(sequence._root, sequence._depth, 0),
                      logs._chunk_at(saved[-1][0]._root, saved[-1][0]._depth, 0))

    def test_index_slice_json_and_detached_copy_match_lists(self):
        entries = [{'message': str(i)} for i in range(513)]
        sequence = logs.LogSequence(entries)
        for index in (0, 127, 128, -1, -129, -513):
            self.assertEqual(sequence[index], entries[index])
        for view in (slice(None), slice(-200, None), slice(None, None, -1),
                     slice(120, 300, 3), slice(500, 20, -11), slice(900, 1000)):
            self.assertEqual(sequence[view], entries[view])
        for index in (-514, 513):
            with self.assertRaises(IndexError):
                sequence[index]
        self.assertEqual(json.loads(json.dumps(sequence, default=logs.json_default)), entries)
        detached = copy.deepcopy(sequence)
        self.assertIsInstance(detached, list)
        detached[0]['message'] = 'changed'
        detached.append({'message': 'external'})
        self.assertEqual(sequence, entries)

    def test_changed_blocks_handle_coalescing_replacement_truncation_and_growth(self):
        rng = random.Random(7)
        for old_count, new_count in ((0, 1), (127, 128), (128, 129), (256, 257),
                                     (1024, 2049), (2049, 129), (513, 513), (129, 0)):
            with self.subTest(old=old_count, new=new_count):
                old = logs.LogSequence(range(old_count))
                appended = old
                for i in range(old_count, new_count):
                    appended = appended.appended(i)
                candidates = [logs.LogSequence(range(new_count))]
                if new_count >= old_count:
                    candidates.append(appended)
                values = list(range(new_count))
                if values:
                    values[rng.randrange(len(values))] = -1
                candidates.append(logs.LogSequence(values))
                for current in candidates:
                    expected = [(i // 128, tuple(current[i:i + 128]))
                                for i in range(0, len(current), 128)
                                if current[i:i + 128] != old[i:i + 128]]
                    self.assertEqual(list(current.changed_chunks(old)), expected)

    def test_append_and_comparison_touch_only_tree_paths_as_history_grows(self):
        for count in (511, 262143, 262144):
            with self.subTest(logs=count):
                old = logs.LogSequence(range(count))
                with patch.object(logs.LogSequence, '__iter__', side_effect=AssertionError('scanned logs')), \
                        patch.object(logs, '_replace_chunk', wraps=logs._replace_chunk) as copied:
                    new = old.appended(count)
                    self.assertLessEqual(copied.call_count, 13)
                with patch.object(logs, '_different_chunks', wraps=logs._different_chunks) as compared:
                    changes = list(new.changed_chunks(old))
                    self.assertEqual(len(changes), 1)
                    self.assertLessEqual(compared.call_count, 25)
                    self.assertLessEqual(len(changes[0][1]), 128)

    def test_private_draft_edits_do_not_modify_published_sequence(self):
        old = logs.LogSequence(range(513))
        draft = logs.LogDraft(old)
        draft.append(513)
        appended = draft.freeze()
        draft.extend([514, 515])
        draft[0] = -1
        draft[1:3] = [99]
        del draft[-1]
        draft.insert(0, 88)
        self.assertEqual(old, list(range(513)))
        self.assertEqual(appended, list(range(514)))
        expected = list(range(516))
        expected[0] = -1
        expected[1:3] = [99]
        del expected[-1]
        expected.insert(0, 88)
        self.assertEqual(draft.freeze(), expected)
