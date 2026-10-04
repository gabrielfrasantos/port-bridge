import unittest

from portbridge.bridge_config import (
    BridgeConfig,
    CanConfig,
    SerialConfig,
    can_channel_or_default,
    can_from_spec,
    find_conflicts,
    find_invalid_values,
    find_problems,
    parse_spec,
    probe_from_spec,
    serial_from_spec,
)
from portbridge.probe_server import ProbeConfig


class TestParseSpec(unittest.TestCase):
    def test_parses_pairs_and_lowercases_keys(self):
        self.assertEqual(
            parse_spec(" Port=COM4 , baud=115200,", ("port", "baud")),
            {"port": ["COM4"], "baud": ["115200"]},
        )

    def test_repeatable_keys_accumulate(self):
        spec = parse_spec("config=a.cfg,config=b.cfg", ("config",))

        self.assertEqual(spec["config"], ["a.cfg", "b.cfg"])

    def test_rejects_unknown_key(self):
        with self.assertRaisesRegex(ValueError, "unknown key 'speed'"):
            parse_spec("speed=1", ("port",))

    def test_rejects_malformed_pair(self):
        with self.assertRaisesRegex(ValueError, "key=value"):
            parse_spec("COM4", ("port",))
        with self.assertRaisesRegex(ValueError, "key=value"):
            parse_spec("port=", ("port",))

    def test_rejects_duplicate_scalar_key(self):
        with self.assertRaisesRegex(ValueError, "more than once"):
            parse_spec("port=COM4,port=COM5", ("port",))


class TestSpecConverters(unittest.TestCase):
    def test_serial_spec_defaults(self):
        self.assertEqual(serial_from_spec("port=/dev/ttyUSB1"), SerialConfig("/dev/ttyUSB1"))

    def test_serial_spec_full(self):
        self.assertEqual(
            serial_from_spec("port=COM4,baud=115200,tcp=5002"),
            SerialConfig("COM4", 115200, 5002),
        )

    def test_serial_spec_requires_port(self):
        with self.assertRaisesRegex(ValueError, "port="):
            serial_from_spec("baud=115200")

    def test_serial_spec_rejects_non_integer(self):
        with self.assertRaisesRegex(ValueError, "'tcp' must be an integer"):
            serial_from_spec("port=COM4,tcp=abc")

    def test_can_spec_indexed_interface_defaults_channel(self):
        cfg = can_from_spec("interface=gs_usb,tcp=5003")

        self.assertEqual(cfg, CanConfig("gs_usb", "0", 125000, 5003, None))

    def test_can_spec_slcan_gets_tty_baudrate(self):
        cfg = can_from_spec("interface=slcan,channel=COM7,tty-baud=2000000")

        self.assertEqual(cfg.tty_baudrate, 2000000)

    def test_can_spec_requires_channel_for_named_interfaces(self):
        with self.assertRaisesRegex(ValueError, "requires a channel"):
            can_from_spec("interface=socketcan")

    def test_can_channel_or_default(self):
        self.assertEqual(can_channel_or_default("candle", None), "0")
        self.assertEqual(can_channel_or_default("pcan", "PCAN_USBBUS2"), "PCAN_USBBUS2")

    def test_probe_spec_stlink(self):
        cfg = probe_from_spec(
            "kind=stlink,serial=066DFF48,gdb=61244,interface=jtag,programmer=/cp", "0.0.0.0"
        )

        self.assertEqual(cfg.kind, "stlink")
        self.assertEqual(cfg.serial_number, "066DFF48")
        self.assertEqual(cfg.resolved_gdb_port, 61244)
        self.assertEqual(cfg.interface, "JTAG")
        self.assertEqual(cfg.programmer_path, "/cp")
        self.assertEqual(cfg.bind_address, "0.0.0.0")

    def test_probe_spec_openocd_board_and_configs(self):
        cfg = probe_from_spec("kind=openocd,board=ek-tm4c123gxl,config=extra.cfg", "127.0.0.1")

        self.assertEqual(cfg.configs, ["board/ek-tm4c123gxl.cfg", "extra.cfg"])

    def test_probe_spec_validation(self):
        cases = {
            "gdb=1": "requires 'kind='",
            "kind=pyocd": "probe kind must be one of",
            "kind=jlink": "requires 'device='",
            "kind=openocd": "requires 'board=' or 'config='",
            "kind=openocd,board=nope": "unknown OpenOCD board",
            "kind=stlink,interface=SPI": "interface must be one of",
        }
        for spec, message in cases.items():
            with self.subTest(spec=spec), self.assertRaisesRegex(ValueError, message):
                probe_from_spec(spec, "127.0.0.1")


class TestFindConflicts(unittest.TestCase):
    def test_distinct_channels_have_no_conflicts(self):
        cfg = BridgeConfig(
            serials=[SerialConfig("COM3", tcp_port=5000), SerialConfig("COM4", tcp_port=5002)],
            cans=[CanConfig("gs_usb", "0", tcp_port=5001), CanConfig("gs_usb", "1", tcp_port=5003)],
            probes=[
                ProbeConfig(kind="jlink"),
                ProbeConfig(kind="stlink"),
                ProbeConfig(kind="stlink", gdb_port=61244),
                ProbeConfig(kind="openocd"),
            ],
        )

        self.assertEqual(find_conflicts(cfg), [])

    def test_reports_tcp_port_shared_across_kinds(self):
        cfg = BridgeConfig(
            serials=[SerialConfig("COM3", tcp_port=3333)],
            probes=[ProbeConfig(kind="openocd")],
        )

        self.assertEqual(
            find_conflicts(cfg), ["TCP port 3333 is used by serial COM3 and openocd probe (GDB)"]
        )

    def test_reports_same_probe_kind_on_default_ports(self):
        cfg = BridgeConfig(probes=[ProbeConfig(kind="jlink"), ProbeConfig(kind="jlink")])

        conflicts = find_conflicts(cfg)

        self.assertEqual(len(conflicts), 2)
        self.assertIn("TCP port 2331", conflicts[0])
        self.assertIn("TCP port 2333", conflicts[1])

    def test_reports_duplicate_devices(self):
        cfg = BridgeConfig(
            serials=[SerialConfig("COM3", tcp_port=5000), SerialConfig("COM3", tcp_port=5002)],
            cans=[CanConfig("pcan", "A", tcp_port=5001), CanConfig("pcan", "A", tcp_port=5003)],
        )

        conflicts = find_conflicts(cfg)

        self.assertEqual(
            conflicts,
            [
                "serial device COM3 is used by serial COM3 and serial COM3",
                "CAN channel pcan:A is used by CAN pcan:A and CAN pcan:A",
            ],
        )

    def test_slcan_channel_claims_its_serial_device(self):
        cfg = BridgeConfig(
            serials=[SerialConfig("COM3", tcp_port=5000)],
            cans=[CanConfig("slcan", "COM3", tcp_port=5001, tty_baudrate=115200)],
        )

        self.assertEqual(
            find_conflicts(cfg), ["serial device COM3 is used by serial COM3 and CAN slcan:COM3"]
        )

    def test_is_empty(self):
        self.assertTrue(BridgeConfig().is_empty)
        self.assertFalse(BridgeConfig(serials=[SerialConfig("COM3")]).is_empty)


class TestFindInvalidValues(unittest.TestCase):
    def test_valid_config_has_no_problems(self):
        cfg = BridgeConfig(
            serials=[SerialConfig("COM3")],
            cans=[CanConfig("slcan", "COM4", tty_baudrate=115200)],
            probes=[ProbeConfig(kind="stlink", speed_khz=1800)],
        )

        self.assertEqual(find_problems(cfg), [])

    def test_reports_out_of_range_ports_and_non_positive_rates(self):
        cfg = BridgeConfig(
            serials=[SerialConfig("COM3", baudrate=0, tcp_port=70000)],
            cans=[CanConfig("pcan", "A", bitrate=-1, tcp_port=0)],
            probes=[ProbeConfig(kind="jlink", gdb_port=65536, speed_khz=0)],
        )

        self.assertEqual(
            find_invalid_values(cfg),
            [
                "serial COM3: TCP port 70000 is outside 1-65535",
                "serial COM3: baud rate must be positive, got 0",
                "CAN pcan:A: TCP port 0 is outside 1-65535",
                "CAN pcan:A: bitrate must be positive, got -1",
                "jlink probe (GDB): TCP port 65536 is outside 1-65535",
                "jlink probe: speed must be positive, got 0",
            ],
        )

    def test_find_problems_lists_values_before_conflicts(self):
        cfg = BridgeConfig(
            serials=[SerialConfig("COM3", tcp_port=0), SerialConfig("COM3", tcp_port=0)]
        )

        problems = find_problems(cfg)

        self.assertIn("outside", problems[0])
        self.assertIn("is used by", problems[-1])


if __name__ == "__main__":
    unittest.main()
