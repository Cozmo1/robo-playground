import socket
from unittest import TestCase
from unittest.mock import patch

import robomaster
from robomaster import Commander


class TestConnection(TestCase):
    def test_get_broadcast_ip(self):
        with patch.object(socket.socket, 'recvfrom', return_value=(b'robot ip 192.168.42.42', ('192.168.42.42', 40101))):
            ip = robomaster.get_broadcast_ip(2)
            self.assertEqual('192.168.42.42', ip)


class TestCommander(TestCase):
    @patch('socket.socket')
    def setUp(self, mock_socket):
        IP = '127.0.0.1'
        TIMEOUT = 42.1234

        m = mock_socket()
        m.recv.return_value = b'ok'
        self.commander = Commander(ip=IP, timeout=TIMEOUT)
        m.settimeout.assert_called_with(TIMEOUT)
        m.connect.assert_called_with((IP, robomaster.CTRL_PORT))
        m.recv.assert_called_with(robomaster.DEFAULT_BUF_SIZE)
        m.send.assert_called_with(b'command;')
        m.recv.assert_called_once()

    def test__is_ok(self):
        self.assertTrue(Commander._is_ok('ok'))
        self.assertFalse(Commander._is_ok('fail'))

    def test_version(self):
        VERSION = '1.2.3.4.5'

        with patch('robomaster.Commander._do', return_value=VERSION) as m:
            self.assertEqual(VERSION, self.commander.version())
            m.assert_called_with('version')

    def test_chassis_speed(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.chassis_speed(1.1, 1.2, 1.3))
            m.assert_called_with('chassis', 'speed', 'x', 1.1, 'y', 1.2, 'z', 1.3)

    def test_robot_mode(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.robot_mode(robomaster.MODE_FREE))
            m.assert_called_with('robot', 'mode', robomaster.MODE_FREE)

    def test_get_robot_mode(self):
        with patch('robomaster.Commander._do', return_value=robomaster.MODE_GIMBAL_LEAD) as m:
            self.assertEqual(robomaster.MODE_GIMBAL_LEAD, self.commander.get_robot_mode())
            m.assert_called_with('robot', 'mode', '?')

    def test_chassis_wheel(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.chassis_wheel(-1, -2, -3, -4))
            m.assert_called_with('chassis', 'wheel', 'w1', -1, 'w2', -2, 'w3', -3, 'w4', -4)

    def test_chassis_wheel_out_of_range(self):
        self.assertRaises(AssertionError, self.commander.chassis_wheel, 0, -2000, -3, -4)

    def test_chassis_move(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.chassis_move(5, 4, 3))
            m.assert_called_with('chassis', 'move', 'x', 5, 'y', 4, 'z', 3)
            self.assertEqual('ok', self.commander.chassis_move(5, 4, 3, 2))
            m.assert_called_with('chassis', 'move', 'x', 5, 'y', 4, 'z', 3, 'vxy', 2)
            self.assertEqual('ok', self.commander.chassis_move(5, 4, 3, 2, 1))
            m.assert_called_with('chassis', 'move', 'x', 5, 'y', 4, 'z', 3, 'vxy', 2, 'vz', 1)

    def test_chassis_move_out_of_range(self):
        self.assertRaises(AssertionError, self.commander.chassis_move, 6)
        self.assertRaises(AssertionError, self.commander.chassis_move, 5, 6)
        self.assertRaises(AssertionError, self.commander.chassis_move, 5, 5, 1801)
        self.assertRaises(AssertionError, self.commander.chassis_move, 5, 5, 5, 3.6)
        self.assertRaises(AssertionError, self.commander.chassis_move, 5, 5, 5, 0)
        self.assertRaises(AssertionError, self.commander.chassis_move, 5, 5, 5, 3.5, 0)
        self.assertRaises(AssertionError, self.commander.chassis_move, 5, 5, 5, 3.5, 601)

    def test_get_chassis_speed(self):
        with patch('robomaster.Commander._do', return_value='1 2 30 100 150 200 250') as m:
            self.assertEqual(robomaster.ChassisSpeed(1, 2, 30, 100, 150, 200, 250), self.commander.get_chassis_speed())
            m.assert_called_with('chassis', 'speed', '?')

    def test_get_chassis_speed_raise(self):
        with patch('robomaster.Commander._do', return_value='fail') as m:
            self.assertRaises(AssertionError, self.commander.get_chassis_speed)

    def test_get_chassis_position(self):
        with patch('robomaster.Commander._do', return_value='1 1.5 20') as m:
            self.assertEqual(robomaster.ChassisPosition(1, 1.5, 20), self.commander.get_chassis_position())
            m.assert_called_with('chassis', 'position', '?')

    def test_get_chassis_position_raise(self):
        with patch('robomaster.Commander._do', return_value='fail') as m:
            self.assertRaises(AssertionError, self.commander.get_chassis_position)

    def test_get_chassis_attitude(self):
        with patch('robomaster.Commander._do', return_value='-20 -50.5 -70') as m:
            self.assertEqual(robomaster.ChassisAttitude(-20, -50.5, -70), self.commander.get_chassis_attitude())
            m.assert_called_with('chassis', 'attitude', '?')

    def test_get_chassis_attitude_raise(self):
        with patch('robomaster.Commander._do', return_value='fail') as m:
            self.assertRaises(AssertionError, self.commander.get_chassis_attitude)

    def test_get_chassis_status(self):
        TRUES = [True for i in range(11)]
        FALSES = [False for i in range(11)]

        with patch('robomaster.Commander._do', return_value='1 1 1 1 1 1 1 1 1 1 1') as m:
            self.assertEqual(robomaster.ChassisStatus(*TRUES), self.commander.get_chassis_status())
            m.assert_called_with('chassis', 'status', '?')

        with patch('robomaster.Commander._do', return_value='0 0 0 0 0 0 0 0 0 0 0') as m:
            self.assertEqual(robomaster.ChassisStatus(*FALSES), self.commander.get_chassis_status())
            m.assert_called_with('chassis', 'status', '?')

    def test_chassis_push_on(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.chassis_push_on(all_freq=5))
            m.assert_called_with('chassis', 'push', 'freq', 5)
            self.assertEqual('ok', self.commander.chassis_push_on(all_freq=10, position_freq=5))
            m.assert_called_with('chassis', 'push', 'freq', 10)
            self.assertEqual('ok', self.commander.chassis_push_on(position_freq=10, attitude_freq=20, status_freq=30))
            m.assert_called_with('chassis', 'push', 'position', 'on', 'pfreq', 10, 'attitude', 'on', 'afreq', 20, 'status', 'on', 'sfreq', 30)

    def test_chassis_push_on_raise(self):
        self.assertRaises(AssertionError, self.commander.chassis_push_on)

    def test_chassis_push_off(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.chassis_push_off(all=True))
            m.assert_called_with('chassis', 'push', 'position', 'off', 'attitude', 'off', 'status', 'off')
            self.assertEqual('ok', self.commander.chassis_push_off(position=True, attitude=True, status=True))
            m.assert_called_with('chassis', 'push', 'position', 'off', 'attitude', 'off', 'status', 'off')
            self.assertEqual('ok', self.commander.chassis_push_off(status=True))
            m.assert_called_with('chassis', 'push', 'status', 'off')

    def test_chassis_push_off_raise(self):
        self.assertRaises(AssertionError, self.commander.chassis_push_off)

    def test_gimbal_speed(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.gimbal_speed(15, 20))
            m.assert_called_with('gimbal', 'speed', 'p', 15, 'y', 20)

    def test_gimbal_speed_raise(self):
        self.assertRaises(AssertionError, self.commander.gimbal_speed, -451, 450)
        self.assertRaises(AssertionError, self.commander.gimbal_speed, 450, 451)
        self.assertRaises(AssertionError, self.commander.gimbal_speed, 451, 450)
        self.assertRaises(AssertionError, self.commander.gimbal_speed, 450, -451)

    def test_gimbal_move(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.gimbal_move(42, -42))
            m.assert_called_with('gimbal', 'move', 'p', 42, 'y', -42)
            self.assertEqual('ok', self.commander.gimbal_move(42, -42, 120))
            m.assert_called_with('gimbal', 'move', 'p', 42, 'y', -42, 'vp', 120)
            self.assertEqual('ok', self.commander.gimbal_move(42, -42, 120, 150))
            m.assert_called_with('gimbal', 'move', 'p', 42, 'y', -42, 'vp', 120, 'vy', 150)

    def test_gimbal_move_raise(self):
        self.assertRaises(AssertionError, self.commander.gimbal_move, 56, 55)
        self.assertRaises(AssertionError, self.commander.gimbal_move, 55, -56)
        self.assertRaises(AssertionError, self.commander.gimbal_move, 0, 0, 541)
        self.assertRaises(AssertionError, self.commander.gimbal_move, 0, 0, 1, 541)

    def test_gimbal_moveto(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.gimbal_moveto(12, -12))
            m.assert_called_with('gimbal', 'moveto', 'p', 12, 'y', -12)
            self.assertEqual('ok', self.commander.gimbal_moveto(12, -12, 120))
            m.assert_called_with('gimbal', 'moveto', 'p', 12, 'y', -12, 'vp', 120)
            self.assertEqual('ok', self.commander.gimbal_moveto(12, -12, 120, 150))
            m.assert_called_with('gimbal', 'moveto', 'p', 12, 'y', -12, 'vp', 120, 'vy', 150)

    def test_gimbal_moveto_raise(self):
        self.assertRaises(AssertionError, self.commander.gimbal_moveto, 56, 55)
        self.assertRaises(AssertionError, self.commander.gimbal_moveto, 55, -56)
        self.assertRaises(AssertionError, self.commander.gimbal_moveto, 0, 0, 541)
        self.assertRaises(AssertionError, self.commander.gimbal_moveto, 0, 0, 1, 541)

    def test_gimbal_suspend(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.gimbal_suspend())
            m.assert_called_with('gimbal', 'suspend')

    def test_gimbal_resume(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.gimbal_resume())
            m.assert_called_with('gimbal', 'resume')

    def test_gimbal_recenter(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.gimbal_recenter())
            m.assert_called_with('gimbal', 'recenter')

    def test_get_gimbal_attitude(self):
        with patch('robomaster.Commander._do', return_value='-10 20') as m:
            self.assertEqual(robomaster.GimbalAttitude(-10, 20), self.commander.get_gimbal_attitude())
            m.assert_called_with('gimbal', 'attitude', '?')

    def test_gimbal_push_on(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.gimbal_push_on(attitude_freq=20))
            m.assert_called_with('gimbal', 'push', 'attitude', 'on', 'afreq', 20)

    def test_gimbal_push_on_raise(self):
        self.assertRaises(AssertionError, self.commander.gimbal_push_on, 17)

    def test_gimbal_push_off(self):
        with patch('robomaster.Commander._do', return_value='ok') as m:
            self.assertEqual('ok', self.commander.gimbal_push_off(True))
            m.assert_called_with('gimbal', 'push', 'attitude', 'off')

    def test_gimbal_push_off_raise(self):
        self.assertRaises(AssertionError, self.commander.chassis_push_off, False)
