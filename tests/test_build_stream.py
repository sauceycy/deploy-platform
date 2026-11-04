import subprocess
import sys
import unittest
from unittest.mock import patch

import server as s


class BuildStreamTests(unittest.TestCase):
    def test_output_is_visible_before_command_finishes_and_is_redacted(self):
        processes = []
        logs = []
        real_popen = subprocess.Popen

        def start(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        def capture(_execution_id, message):
            logs.append((message, processes[0].poll()))

        with patch.object(s.subprocess, 'Popen', side_effect=start), \
                patch.object(s, 'append_log', side_effect=capture), \
                patch.object(s, 'is_execution_cancelled', return_value=False):
            code, output, elapsed = s.run_command_stream(
                [sys.executable, '-u', '-c',
                 "import time; print('dependency token-secret', flush=True); time.sleep(2); print('finished'); raise SystemExit(2)"],
                'test', redact=lambda text: text.replace('token-secret', '***'),
            )
        self.assertEqual(code, 2)
        self.assertTrue(any('dependency ***' in text and status is None for text, status in logs))
        self.assertFalse(any('token-secret' in text for text, _ in logs))
        self.assertTrue(any('finished' in text for text, _ in logs))
        self.assertIn('finished', output)
        self.assertGreaterEqual(elapsed, 2)


if __name__ == '__main__':
    unittest.main()
