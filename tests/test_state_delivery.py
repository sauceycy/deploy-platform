import socket
import threading
import unittest
from unittest.mock import Mock, patch

import server as s


class StateDeliveryTests(unittest.TestCase):
    def test_slow_browser_does_not_block_state_mutation_or_other_browsers(self):
        entered = threading.Event()
        release = threading.Event()
        delivered = threading.Event()
        completed = threading.Event()
        failures = []

        def blocked_send(_frame):
            entered.set()
            release.wait(3)

        slow_socket = Mock(sendall=blocked_send)
        fast_socket = Mock()
        fast_socket.sendall.side_effect = lambda _frame: delivered.set()
        slow = s.WebSocketClient(slow_socket)
        fast = s.WebSocketClient(fast_socket)
        senders = [threading.Thread(target=client.send_loop, daemon=True) for client in (slow, fast)]

        def save():
            try:
                s.mutate_state(lambda state: state['auditLogs'].append({'action': 'save'}))
                self.assertEqual(s.read_state()['auditLogs'][-1]['action'], 'save')
            except Exception as exc:
                failures.append(exc)
            finally:
                completed.set()

        with patch.object(s, 'WS_CLIENTS', {slow, fast}), \
                patch.object(s, 'STATE_CACHE', s.default_state_copy()), \
                patch.object(s, 'enqueue_state_write'):
            for sender in senders:
                sender.start()
            worker = threading.Thread(target=save, daemon=True)
            try:
                s.broadcast_ws({'type': 'state', 'state': {}})
                self.assertTrue(entered.wait(1))
                worker.start()
                self.assertTrue(completed.wait(1), 'state updates waited for a browser socket')
                self.assertTrue(delivered.wait(1), 'a healthy browser waited for a slow browser')
                self.assertFalse(release.is_set())
                self.assertEqual(failures, [])
            finally:
                release.set()
                slow.close()
                fast.close()
                for sender in senders:
                    sender.join(2)
                if worker.ident:
                    worker.join(2)

    def test_backpressure_disconnects_only_the_slow_client(self):
        slow = s.WebSocketClient(Mock())
        fast = s.WebSocketClient(Mock())
        for _ in range(slow.frames.maxsize):
            self.assertTrue(slow.enqueue(b'frame'))
        with patch.object(s, 'WS_CLIENTS', {slow, fast}):
            s.broadcast_ws({'type': 'state'})
            self.assertTrue(slow.stopped.is_set())
            self.assertNotIn(slow, s.WS_CLIENTS)
            self.assertIn(fast, s.WS_CLIENTS)
            self.assertEqual(fast.frames.qsize(), 1)
            slow.connection.sendall.assert_not_called()
            slow.connection.shutdown.assert_not_called()

    def test_sender_preserves_frame_order(self):
        connection = Mock()
        client = s.WebSocketClient(connection)
        client.enqueue(b'initial')
        client.enqueue(b'update')
        connection.sendall.side_effect = lambda frame: client.stopped.set() if frame == b'update' else None
        client.send_loop()
        self.assertEqual([call.args[0] for call in connection.sendall.call_args_list], [b'initial', b'update'])

    def test_send_timeout_removes_client_and_unblocks_reader(self):
        connection = Mock()
        connection.sendall.side_effect = socket.timeout()
        client = s.WebSocketClient(connection)
        client.enqueue(b'frame')
        with patch.object(s, 'WS_CLIENTS', {client}):
            client.send_loop()
            self.assertTrue(client.stopped.is_set())
            self.assertNotIn(client, s.WS_CLIENTS)
            connection.shutdown.assert_called_with(socket.SHUT_RDWR)

    def test_database_connect_and_queries_have_time_limits(self):
        driver = Mock()
        with patch.object(s, 'psycopg', driver):
            connection = s.connect_postgres()
        kwargs = driver.connect.call_args.kwargs
        self.assertEqual(kwargs['connect_timeout'], 5)
        self.assertIn('statement_timeout=10000', kwargs['options'])
        self.assertIn('lock_timeout=3000', kwargs['options'])
        connection.commit.assert_called_once()

    def test_failed_database_setup_closes_connection(self):
        driver = Mock()
        driver.connect.return_value.execute.side_effect = RuntimeError('statement timeout')
        with patch.object(s, 'psycopg', driver):
            with self.assertRaisesRegex(RuntimeError, 'statement timeout'):
                s.connect_postgres()
        driver.connect.return_value.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
