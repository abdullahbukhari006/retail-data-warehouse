"""Operator transitions, reporting, and coordinator shutdown."""
from pathlib import Path
from queue import Queue
import sys
from threading import Event, Thread
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retail_dw.commands import OperatorConsole
from retail_dw.execution import RunControl
from retail_dw.online import OnlineThread
from retail_dw.runtime import execute_threads
from retail_dw.settings import RuntimeSettings
from test_threads import ThreadFixture


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.control = RunControl(RuntimeSettings())
        self.output = []
        self.console = OperatorConsole(self.control, Queue(), write=self.output.append)

    def test_commands_report_modes_rates_and_actual_counts(self):
        for mode in ('online', 'periodic', 'both'):
            self.console.execute(mode)
            self.assertEqual(self.control.operator_snapshot()['mode'], mode)
            self.assertIn('Mode: '+mode, self.output[-1])
        self.console.execute('peak 125')
        self.console.execute('non-peak 25')
        self.assertEqual(self.control.operator_snapshot()['peak_rate'], 125)
        self.assertEqual(self.control.operator_snapshot()['non_peak_rate'], 25)
        self.control.set_stats(hash_transactions=17, shed_pending=9)
        self.console.buffer.put(object())
        self.console.execute('status')
        self.assertIn('Speed: non-peak (25/second)', self.output[-1])
        self.assertIn('Stream buffer: 1/1000', self.output[-1])
        self.assertIn('H transactions: 17', self.output[-1])
        self.assertIn('Shed disk: 9', self.output[-1])

    def test_invalid_commands_leave_state_unchanged(self):
        before = self.control.operator_snapshot()
        for command in ('peak 0', 'non-peak -1', 'peak abc', 'both extra', 'unknown'):
            self.console.execute(command)
            self.assertEqual(self.control.operator_snapshot(), before)
        self.console.execute(' HeLP ')
        self.assertIn('Commands:', self.output[-1])

    def test_eof_becomes_graceful_quit(self):
        def eof(prompt):
            raise EOFError
        self.console.read = eof
        self.console.start()
        self.console.reader.join(1)
        self.console.poll()
        self.assertTrue(self.control.stop.is_set())
        self.assertFalse(self.console.reader.is_alive())

    def test_paused_channel_wakes_on_resume_and_stop(self):
        self.control.set_mode('periodic')
        returned = Event()
        worker = Thread(target=lambda: (self.control.wait_for_channel('online'), returned.set()))
        worker.start()
        self.assertFalse(returned.wait(.1))
        self.control.set_mode('both')
        self.assertTrue(returned.wait(1))
        worker.join(1)
        self.control.set_mode('online')
        worker = Thread(target=lambda: self.control.wait_for_channel('periodic'))
        worker.start()
        self.control.stop.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())


class OperatorThreadTests(ThreadFixture, unittest.TestCase):
    def test_online_pauses_without_losing_or_repeating_rows(self):
        control = RunControl(self.settings)
        control.set_mode('periodic')
        buffer = Queue(1000)
        online = OnlineThread(self.root/'online_sales_stream.csv', self.root/'assigned_sales.csv', buffer, control, 5)
        online.start()
        self.assertFalse(online.finished.wait(.1))
        self.assertEqual(control.snapshot()['online_submitted'], 0)
        control.set_mode('both')
        online.join(2)
        self.assertFalse(online.is_alive())
        self.assertEqual([buffer.get_nowait().source_line_id for _ in range(5)], list(range(2, 7)))

    def test_interactive_coordinator_keeps_console_after_completion(self):
        output = []
        stages = []
        class ScriptedConsole(OperatorConsole):
            def start(inner):
                stages.append('online')
            def poll(inner):
                stats = inner.control.snapshot()
                if stages[-1] == 'online' and stats['online_submitted'] == 5:
                    self.assertEqual(stats['groups_released'], 0)
                    inner.execute('periodic')
                    stages.append('periodic')
                elif stages[-1] == 'periodic' and stats['groups_released'] == 1:
                    inner.execute('both')
                    inner.execute('peak 500')
                    inner.execute('non-peak 1000')
                    stages.append('both')
                elif stats.get('workers_complete'):
                    inner.execute('status')
                    inner.execute('quit')
                    stages.append('quit')
        def factory(control, buffer):
            return ScriptedConsole(control, buffer, write=output.append)
        with patch('mysql.connector.connect', side_effect=self.connect):
            result = execute_threads(self.config, self.root, self.root/'online_sales_stream.csv',
                                     self.settings, 5, 2, interactive=True,
                                     initial_mode='online', console_factory=factory)
        self.assertEqual(stages, ['online', 'periodic', 'both', 'quit'])
        self.assertEqual(result['enriched_online'], 5)
        self.assertEqual(result['enriched_periodic'], 6)
        self.assertFalse(result['cancelled'])
        self.assertTrue(any('State: complete' in line for line in output))
        self.assertTrue(all(connection.closed for _, connection in self.connections))

    def test_quit_during_input_drains_and_closes_workers(self):
        class QuitConsole(OperatorConsole):
            def start(inner):
                pass
            def poll(inner):
                if inner.control.snapshot()['online_submitted'] >= 3:
                    inner.execute('quit')
        with patch('mysql.connector.connect', side_effect=self.connect):
            result = execute_threads(self.config, self.root, self.root/'online_sales_stream.csv',
                                     self.settings, 20, 2, interactive=True,
                                     console_factory=lambda c,b: QuitConsole(c,b,write=lambda line: None))
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['online_submitted'], result['enriched_online'])
        self.assertEqual(result['stream_buffer'], 0)
        self.assertTrue(all(connection.closed for _, connection in self.connections))
