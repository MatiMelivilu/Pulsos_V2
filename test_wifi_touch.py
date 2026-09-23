import subprocess
import unittest
from unittest.mock import Mock, patch

from wifi_touch import WifiWindow, parse_networks, run_nmcli


class WifiTests(unittest.TestCase):
    def test_scan_parses_escaped_ssids_and_prefers_active_network(self):
        output = (
            ':Local\\: caja\\\\1:85:WPA2\n'
            '*:Casa:30:WPA2\n'
            ':Casa:90:WPA2\n'
            '::50:WPA2\n'
            ':Abierta:70:--\n'
            ':Invalida:unknown:WPA2\n'
        )
        self.assertEqual(parse_networks(output), [
            ('Casa', 30, 'WPA2', True),
            ('Local: caja\\1', 85, 'WPA2', False),
            ('Abierta', 70, '--', False),
        ])

    @patch('wifi_touch.subprocess.run')
    def test_password_is_only_passed_on_stdin(self, run):
        run.return_value = Mock(returncode=0, stdout='connected')
        self.assertEqual(run_nmcli(
            ['--ask', 'device', 'wifi', 'connect', 'Red con espacios'],
            password='clave$ especial',
        ), 'connected')
        args, kwargs = run.call_args
        self.assertNotIn('clave$ especial', args[0])
        self.assertEqual(kwargs['input'], 'clave$ especial\n')
        self.assertEqual(kwargs['timeout'], 60)
        self.assertNotIn('shell', kwargs)

    @patch('wifi_touch.subprocess.run')
    def test_connection_errors_do_not_expose_credentials(self, run):
        run.return_value = Mock(returncode=4, stderr='secret-password')
        with self.assertRaises(RuntimeError) as error:
            run_nmcli(['--ask', 'device', 'wifi', 'connect', 'Casa'], 'secret-password')
        self.assertNotIn('secret-password', str(error.exception))

    @patch('wifi_touch.subprocess.run', side_effect=subprocess.TimeoutExpired('nmcli', 60))
    def test_timeout_is_not_reported_as_success(self, run):
        with self.assertRaises(subprocess.TimeoutExpired):
            run_nmcli(['radio', 'wifi', 'on'])

    @patch('wifi_touch.run_nmcli')
    def test_selected_ssid_and_password_are_captured_before_background_job(self, run):
        window = Mock(busy=False, networks=[('Mi Red', 90, 'WPA2', False)])
        window.listbox.curselection.return_value = (0,)
        window.password.get.return_value = 'contraseña'
        WifiWindow.connect(window)
        window.password.set.assert_called_once_with('')
        kind, job = window.start_job.call_args.args
        self.assertEqual(kind, 'connect')
        self.assertEqual(job(), 'Mi Red')
        run.assert_called_once_with(
            ['--ask', 'device', 'wifi', 'connect', 'Mi Red'], password='contraseña')

    def test_no_network_selected_does_not_start_connection(self):
        window = Mock(busy=False)
        window.listbox.curselection.return_value = ()
        WifiWindow.connect(window)
        window.start_job.assert_not_called()


if __name__ == '__main__':
    unittest.main()
