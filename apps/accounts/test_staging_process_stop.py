"""No database is needed to prove bounded termination of our own HTTP subprocess."""
import io
import os
import signal
import subprocess
import sys
from unittest import TestCase, skipIf
from unittest.mock import Mock
from ops.staging_updates import stop_http


class StagingProcessStopTests(TestCase):
    def child(self, waits, poll=None):
        child=Mock();child.stdin=io.BytesIO();child.wait.side_effect=waits;child.poll.return_value=poll;child.pid=123
        return child

    def test_normal_exit_never_terminates(self):
        child=self.child([0]);stop_http(child)
        self.assertTrue(child.stdin.closed);child.terminate.assert_not_called();child.kill.assert_not_called()
        child.wait.assert_called_once_with(timeout=115)

    def test_exact_reported_115_second_timeout_terminates_only_owned_child(self):
        child=self.child([subprocess.TimeoutExpired('synthetic-test-http',115),0])
        stop_http(child);child.terminate.assert_called_once_with();child.kill.assert_not_called()
        self.assertEqual([c.kwargs['timeout'] for c in child.wait.call_args_list],[115,15])

    def test_kill_only_after_termination_timeout(self):
        child=self.child([subprocess.TimeoutExpired('http',115),subprocess.TimeoutExpired('http',15),0])
        stop_http(child);child.terminate.assert_called_once_with();child.kill.assert_called_once_with()
        self.assertEqual([c.kwargs['timeout'] for c in child.wait.call_args_list],[115,15,15])

    def test_failed_kill_is_propagated_and_cannot_look_like_a_stop(self):
        child=self.child([subprocess.TimeoutExpired('http',115),subprocess.TimeoutExpired('http',15),subprocess.TimeoutExpired('http',15)])
        with self.assertRaises(subprocess.TimeoutExpired):stop_http(child)

    def test_already_closed_pipe_and_just_exited_child_are_safe(self):
        child=self.child([subprocess.TimeoutExpired('http',115),0],poll=0);child.stdin.close()
        stop_http(child);child.terminate.assert_not_called();child.kill.assert_not_called()

    def test_access_denied_is_not_ignored(self):
        child=self.child([subprocess.TimeoutExpired('http',115)]);child.terminate.side_effect=PermissionError('synthetic denied')
        with self.assertRaises(PermissionError):stop_http(child)
        child.kill.assert_not_called()

    def run_owned_process(self,ignore_term=False):
        script="import signal,sys,time\n"+("signal.signal(signal.SIGTERM,signal.SIG_IGN)\n" if ignore_term else '')+"print('READY',flush=True)\nwhile True:time.sleep(.05)\n"
        child=subprocess.Popen([sys.executable,'-u','-c',script],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline().rstrip(b'\r\n'),b'READY')
            stop_http(child,graceful_timeout=.15,terminate_timeout=.15,kill_timeout=3)
            self.assertIsNotNone(child.poll())
            if ignore_term:self.assertEqual(child.returncode,-signal.SIGKILL)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=3)
            for pipe in [child.stdin,child.stdout,child.stderr]:
                if pipe and not pipe.closed:pipe.close()

    def test_real_process_ignoring_stdin_eof_does_not_block_update(self):
        self.run_owned_process()

    @skipIf(os.name=='nt','Windows terminate already uses TerminateProcess')
    def test_real_process_ignoring_sigterm_is_killed(self):
        self.run_owned_process(ignore_term=True)
