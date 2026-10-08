import asyncio
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from portbridge import probe_server
from portbridge.probe_server import (
    DebugProbeServer,
    ProbeConfig,
    build_jlink_command,
    build_openocd_command,
    build_stlink_command,
)
from portbridge.server_errors import (
    HardwareUnavailableError,
    PortUnavailableError,
    ToolNotFoundError,
)


class FakeProcess:
    def __init__(self, lines=(), exit_code=None, ignore_terminate=False):
        self.stdout = asyncio.StreamReader()
        for line in lines:
            self.stdout.feed_data(line.encode() + b"\n")
        self.returncode = None
        self.terminated = False
        self.killed = False
        self._ignore_terminate = ignore_terminate
        self._exit = asyncio.Event()
        if exit_code is not None:
            self.finish(exit_code)

    def finish(self, code):
        if self.returncode is None:
            self.returncode = code
            self.stdout.feed_eof()
            self._exit.set()

    async def wait(self):
        await self._exit.wait()
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self._ignore_terminate:
            self.finish(0)

    def kill(self):
        self.killed = True
        self.finish(-9)


def make_factory(proc, calls=None):
    async def factory(*argv, **kwargs):
        if calls is not None:
            calls.append((argv, kwargs))
        return proc

    return factory


async def port_closed(_host, _port):
    return False


def openocd_config(**overrides):
    values = {"kind": "openocd", "configs": ["board/ek-tm4c123gxl.cfg"]}
    values.update(overrides)
    return ProbeConfig(**values)


def jlink_config(**overrides):
    values = {"kind": "jlink", "device": "TM4C123GH6PM"}
    values.update(overrides)
    return ProbeConfig(**values)


def stlink_config(**overrides):
    values = {"kind": "stlink"}
    values.update(overrides)
    return ProbeConfig(**values)


def make_server(cfg, proc, calls=None, port_probe=port_closed, **kwargs):
    return DebugProbeServer(
        cfg,
        process_factory=make_factory(proc, calls),
        port_probe=port_probe,
        tool_finder=lambda _cfg: "/usr/bin/tool",
        **kwargs,
    )


class TestCommandBuilders(unittest.TestCase):
    def test_jlink_loopback_bind_is_localhost_only(self):
        cmd = build_jlink_command(jlink_config(), "JLinkGDBServerCLExe")

        self.assertEqual(
            cmd,
            [
                "JLinkGDBServerCLExe",
                "-device",
                "TM4C123GH6PM",
                "-if",
                "SWD",
                "-speed",
                "4000",
                "-port",
                "2331",
                "-telnetport",
                "2333",
                "-LocalhostOnly",
                "1",
            ],
        )

    def test_jlink_non_loopback_bind_listens_on_all_interfaces(self):
        cmd = build_jlink_command(jlink_config(bind_address="0.0.0.0"), "jl")

        self.assertEqual(cmd[-2:], ["-LocalhostOnly", "0"])

    def test_jlink_serial_speed_ports_and_interface(self):
        cfg = jlink_config(
            serial_number="801012345",
            speed_khz=1000,
            interface="jtag",
            gdb_port=5331,
            telnet_port=5333,
        )

        cmd = build_jlink_command(cfg, "jl")

        self.assertIn("-select", cmd)
        self.assertEqual(cmd[cmd.index("-select") + 1], "USB=801012345")
        self.assertEqual(cmd[cmd.index("-speed") + 1], "1000")
        self.assertEqual(cmd[cmd.index("-if") + 1], "JTAG")
        self.assertEqual(cmd[cmd.index("-port") + 1], "5331")
        self.assertEqual(cmd[cmd.index("-telnetport") + 1], "5333")

    def test_jlink_without_serial_has_no_select(self):
        self.assertNotIn("-select", build_jlink_command(jlink_config(), "jl"))

    def test_jlink_requires_device(self):
        with self.assertRaises(ValueError):
            build_jlink_command(jlink_config(device=None), "jl")

    def test_jlink_rejects_unknown_interface(self):
        with self.assertRaises(ValueError):
            build_jlink_command(jlink_config(interface="cJTAG"), "jl")

    def test_openocd_argument_order(self):
        cfg = openocd_config(
            configs=["interface/ti-icdi.cfg", "target/stellaris.cfg"],
            search_dirs=["/boards"],
            commands=["init", "reset halt"],
            speed_khz=500,
            bind_address="0.0.0.0",
        )

        cmd = build_openocd_command(cfg, "openocd")

        self.assertEqual(
            cmd,
            [
                "openocd",
                "-s",
                "/boards",
                "-c",
                "bindto 0.0.0.0",
                "-c",
                "gdb_port 3333",
                "-c",
                "telnet_port 4444",
                "-c",
                "tcl_port disabled",
                "-f",
                "interface/ti-icdi.cfg",
                "-f",
                "target/stellaris.cfg",
                "-c",
                "adapter speed 500",
                "-c",
                "init",
                "-c",
                "reset halt",
            ],
        )

    def test_openocd_resolves_presets(self):
        cmd = build_openocd_command(openocd_config(configs=["ek-tm4c1294xl"]), "openocd")

        self.assertEqual(cmd[cmd.index("-f") + 1], "board/ek-tm4c1294xl.cfg")

    def test_openocd_requires_config(self):
        with self.assertRaises(ValueError):
            build_openocd_command(openocd_config(configs=[]), "openocd")

    def test_stlink_defaults_to_swd_on_default_port(self):
        cmd = build_stlink_command(stlink_config(), "ST-LINK_gdbserver", "/cp/bin")

        self.assertEqual(cmd, ["ST-LINK_gdbserver", "-p", "61234", "-cp", "/cp/bin", "-e", "-d"])

    def test_stlink_jtag_serial_speed_and_port(self):
        cfg = stlink_config(
            interface="jtag", serial_number="0670FF48", speed_khz=1800, gdb_port=61244
        )

        cmd = build_stlink_command(cfg, "st", "/cp")

        self.assertNotIn("-d", cmd)
        self.assertEqual(cmd[cmd.index("-p") + 1], "61244")
        self.assertEqual(cmd[cmd.index("-i") + 1], "0670FF48")
        self.assertEqual(cmd[cmd.index("--frequency") + 1], "1800")

    def test_stlink_rejects_unknown_interface(self):
        with self.assertRaises(ValueError):
            build_stlink_command(stlink_config(interface="SPI"), "st", "/cp")

    def test_stlink_connect_modes(self):
        for mode, flags in (("normal", []), ("under-reset", ["-k"]), ("HOTPLUG", ["-g"])):
            with self.subTest(mode=mode):
                cmd = build_stlink_command(stlink_config(connect_mode=mode), "st", "/cp")

                self.assertEqual(cmd, ["st", "-p", "61234", "-cp", "/cp", "-e", "-d", *flags])

    def test_stlink_access_port_defaults_from_device_family(self):
        for device, flags in (
            (None, []),
            ("STM32F446RE", []),
            ("STM32WBA55CG", ["-m", "1"]),
            ("stm32h563zi", ["-m", "1"]),
            ("STM32H7S3L8", ["-m", "1"]),
            ("STM32H743ZI", []),
        ):
            with self.subTest(device=device):
                cmd = build_stlink_command(stlink_config(device=device), "st", "/cp")

                self.assertEqual(cmd, ["st", "-p", "61234", "-cp", "/cp", "-e", "-d", *flags])

    def test_stlink_explicit_access_port_wins(self):
        cmd = build_stlink_command(
            stlink_config(device="STM32WBA55CG", access_port=0, connect_mode="under-reset"),
            "st",
            "/cp",
        )
        self.assertEqual(cmd, ["st", "-p", "61234", "-cp", "/cp", "-e", "-d", "-k"])

        cmd = build_stlink_command(stlink_config(access_port=2), "st", "/cp")
        self.assertEqual(cmd[-2:], ["-m", "2"])

    def test_stlink_rejects_out_of_range_access_port(self):
        with self.assertRaises(ValueError):
            build_stlink_command(stlink_config(access_port=256), "st", "/cp")

    def test_stlink_rejects_unknown_connect_mode(self):
        with self.assertRaises(ValueError):
            build_stlink_command(stlink_config(connect_mode="powerdown"), "st", "/cp")

    def test_stlink_has_no_telnet_port(self):
        self.assertIsNone(stlink_config().resolved_telnet_port)

    def test_build_probe_command_resolves_cubeprogrammer(self):
        with mock.patch.object(
            probe_server, "find_stm32cubeprogrammer", return_value="/cp/bin"
        ) as finder:
            cmd = probe_server.build_probe_command(stlink_config(programmer_path="/x"), "st")

        finder.assert_called_once_with("/x", "st")
        self.assertEqual(cmd[cmd.index("-cp") + 1], "/cp/bin")

    def test_rtt_is_off_unless_configured(self):
        self.assertNotIn("-RTTTelnetPort", build_jlink_command(jlink_config(), "jl"))
        self.assertFalse(
            any("rtt" in arg for arg in build_openocd_command(openocd_config(), "openocd"))
        )

    def test_jlink_rtt_port(self):
        cmd = build_jlink_command(jlink_config(rtt_port=19021), "jl")

        self.assertEqual(cmd[cmd.index("-RTTTelnetPort") + 1], "19021")
        self.assertEqual(cmd[-2:], ["-LocalhostOnly", "1"])

    def test_openocd_rtt_uses_default_search_range_and_channel_zero(self):
        cmd = build_openocd_command(openocd_config(rtt_port=19021), "openocd")

        self.assertEqual(
            cmd[-8:],
            [
                "-c",
                "init",
                "-c",
                'rtt setup 0x20000000 0x10000 "SEGGER RTT"',
                "-c",
                "rtt start",
                "-c",
                "rtt server start 19021 0",
            ],
        )

    def test_openocd_rtt_custom_search_range(self):
        cfg = openocd_config(rtt_port=9090, rtt_address=0x1FFF0000, rtt_size=4096)

        cmd = build_openocd_command(cfg, "openocd")

        self.assertIn('rtt setup 0x1fff0000 0x1000 "SEGGER RTT"', cmd)
        self.assertIn("rtt server start 9090 0", cmd)

    def test_openocd_rtt_runs_after_user_commands(self):
        cfg = openocd_config(
            rtt_port=19021, commands=["transport select swd"], speed_khz=500, bind_address="0.0.0.0"
        )

        cmd = build_openocd_command(cfg, "openocd")

        self.assertLess(cmd.index("transport select swd"), cmd.index("init"))
        self.assertLess(cmd.index("adapter speed 500"), cmd.index("init"))
        self.assertIn("bindto 0.0.0.0", cmd)

    def test_stlink_rejects_rtt(self):
        with self.assertRaisesRegex(ValueError, "interface/stlink.cfg"):
            build_stlink_command(stlink_config(rtt_port=19021), "st", "/cp")


CUBEPROGRAMMER_F446 = """
      -------------------------------------------------------------------
                        STM32CubeProgrammer v2.17.0
      -------------------------------------------------------------------

ST-LINK SN  : 066DFF485550755187121723
ST-LINK FW  : V2J43M28
Board       : NUCLEO-F446RE
Voltage     : 3.25V
SWD freq    : 4000 KHz
Connect mode: Hot Plug
Reset mode  : Software reset
Device ID   : 0x421
Revision ID : Rev A
Device name : STM32F446xC/E
Flash size  : 512 KBytes
Device type : MCU
Device CPU  : Cortex-M4
"""


class TestStDevices(unittest.TestCase):
    def test_normalize_accepts_stm32_names(self):
        for name, expected in (
            ("STM32F446RE", "STM32F446RE"),
            (" stm32g431rb ", "STM32G431RB"),
            ("STM32WB55RG", "STM32WB55RG"),
            ("STM32MP157C", "STM32MP157C"),
            ("STM32WBA52CG", "STM32WBA52CG"),
            ("STM32WLE5J8", "STM32WLE5J8"),
            ("STM32F4", "STM32F4"),
        ):
            with self.subTest(name=name):
                self.assertEqual(probe_server.normalize_st_device(name), expected)

    def test_normalize_rejects_other_vendors(self):
        for name in (
            "TM4C123GH6PM",
            "LPC1768",
            "MK64FN1M0VLL12",
            "NRF52840",
            "ATSAMD21G18A",
            "GD32F303CC",
            "STM8S105",
            "STM32",
            "",
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "STM32 devices only"):
                probe_server.normalize_st_device(name)

    def test_suggested_devices_are_all_valid(self):
        for name in probe_server.STLINK_DEVICES:
            self.assertEqual(probe_server.normalize_st_device(name), name)

    def test_device_matches_reported_name(self):
        cases = {
            ("STM32F446RE", "STM32F446xC/E"): True,
            ("STM32F446RC", "STM32F446xC/E"): True,
            ("STM32F4", "STM32F446xC/E"): True,
            ("STM32F407VG", "STM32F405xx/F407xx/F415xx/F417xx"): True,
            ("STM32F417IG", "STM32F405xx/F407xx/F415xx/F417xx"): True,
            ("STM32F767ZI", "STM32F76x/F77x"): True,
            ("STM32G431RB", "STM32G43x/G44x"): True,
            ("STM32WB35CE", "STM32WB5x/35xx"): True,
            ("STM32WB55RG", "STM32WB5x/35xx"): True,
            ("STM32WB15CC", "STM32WB5x/35xx"): False,
            ("STM32G0B1RE", "STM32G0B0xx/B1xx/C1xx"): True,
            ("STM32G0C1VE", "STM32G0B0xx/B1xx/C1xx"): True,
            ("STM32G0B0RE", "STM32G0B0xx/B1xx/C1xx"): True,
            ("STM32G071RB", "STM32G0B0xx/B1xx/C1xx"): False,
            ("STM32B1", "STM32G0B0xx/B1xx/C1xx"): False,
            ("STM32F030R8", "STM32F05x/F030x8"): True,
            ("STM32F051R8", "STM32F05x/F030x8"): True,
            ("STM32F070RB", "STM32F05x/F030x8"): False,
            ("STM32WBA52CG", "STM32WBA52xx/WBA54xx/WBA55xx"): True,
            ("STM32WBA55CG", "STM32WBA52xx/WBA54xx/WBA55xx"): True,
            ("STM32WLE5J8", "STM32WLE5xx/WLE4xx"): True,
            ("STM32WL55JC", "STM32WLE5xx/WLE4xx"): False,
            ("STM32F446RE", "STM32F401xD/E"): False,
            ("STM32F446RE", "STM32G43x/G44x"): False,
            ("STM32F446RA", "STM32F446xC/E"): False,
            ("STM32L476RG", "STM32F405xx/F407xx/F415xx/F417xx"): False,
        }
        for (device, reported), expected in cases.items():
            with self.subTest(device=device, reported=reported):
                self.assertIs(probe_server.st_device_matches(device, reported), expected)

    def test_parse_cubeprogrammer_target(self):
        self.assertEqual(
            probe_server.parse_cubeprogrammer_target(CUBEPROGRAMMER_F446),
            {"Device ID": "0x421", "Device name": "STM32F446xC/E"},
        )

    def test_build_stlink_command_rejects_non_st_device(self):
        with self.assertRaisesRegex(ValueError, "STM32 devices only"):
            build_stlink_command(stlink_config(device="TM4C123GH6PM"), "st", "/cp")

    def test_build_stlink_command_has_no_device_argument(self):
        cmd = build_stlink_command(stlink_config(device="STM32F446RE"), "st", "/cp")
        self.assertNotIn("STM32F446RE", cmd)


class TestVerifyStlinkTarget(unittest.TestCase):
    def _verify(self, output, returncode=0, **overrides):
        values = {"device": "STM32F446RE"}
        values.update(overrides)
        completed = subprocess.CompletedProcess([], returncode, stdout=output, stderr="")
        with (
            mock.patch.object(probe_server, "find_stm32cubeprogrammer", return_value="/cp"),
            mock.patch.object(probe_server.subprocess, "run", return_value=completed) as run,
        ):
            found = probe_server.verify_stlink_target(stlink_config(**values), "/st/gdb")
        return found, run.call_args.args[0]

    def test_matching_target_connects_hot_plug(self):
        found, argv = self._verify(
            CUBEPROGRAMMER_F446, serial_number="066DFF48", interface="jtag", speed_khz=1800
        )

        self.assertEqual(found, "STM32F446xC/E (ID 0x421)")
        self.assertTrue(argv[0].startswith(str(Path("/cp") / "STM32_Programmer_CLI")))
        self.assertEqual(argv[1:], ["-c", "port=JTAG", "mode=HOTPLUG", "sn=066DFF48", "freq=1800"])

    def test_under_reset_verifies_under_hardware_reset(self):
        for mode, expected in (
            ("under-reset", ["mode=UR", "reset=HWrst"]),
            ("hotplug", ["mode=HOTPLUG"]),
            ("normal", ["mode=HOTPLUG"]),
        ):
            with self.subTest(mode=mode):
                _, argv = self._verify(CUBEPROGRAMMER_F446, connect_mode=mode)

                self.assertEqual(argv[1:], ["-c", "port=SWD", *expected])

    def test_explicit_access_port_is_passed_to_cubeprogrammer(self):
        _, argv = self._verify(CUBEPROGRAMMER_F446, access_port=1)

        self.assertEqual(argv[1:], ["-c", "port=SWD", "mode=HOTPLUG", "ap=1"])

    def test_mismatching_target_raises(self):
        with self.assertRaisesRegex(
            HardwareUnavailableError, r"STM32F446xC/E \(ID 0x421\), not the configured STM32L476RG"
        ):
            self._verify(CUBEPROGRAMMER_F446, device="stm32l476rg")

    def test_unreadable_target_raises_with_output(self):
        with self.assertRaisesRegex(HardwareUnavailableError, "No STM32 target found"):
            self._verify("Error: No STM32 target found!", returncode=1)

    def test_nonzero_exit_is_rejected_even_with_device_fields(self):
        output = CUBEPROGRAMMER_F446 + "Error: failed to connect to the target"
        with self.assertRaisesRegex(
            HardwareUnavailableError, "exited with code 1: .*failed to connect to the target"
        ):
            self._verify(output, returncode=1)

    def test_launch_failure_raises(self):
        with (
            mock.patch.object(probe_server, "find_stm32cubeprogrammer", return_value="/cp"),
            mock.patch.object(probe_server.subprocess, "run", side_effect=OSError("denied")),
            self.assertRaisesRegex(HardwareUnavailableError, "denied"),
        ):
            probe_server.verify_stlink_target(stlink_config(device="STM32F446RE"), "/st/gdb")


class TestToolDiscovery(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_exe(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        path.chmod(0o755)
        return path

    def test_override_file_is_used(self):
        exe = self._make_exe(self.tmp / "my-openocd")

        self.assertEqual(Path(probe_server.find_openocd(str(exe))), exe)

    def test_override_folder_appends_executable_name(self):
        with mock.patch.object(sys, "platform", "linux"):
            exe = self._make_exe(self.tmp / "JLinkGDBServerCLExe")

            found = probe_server.find_jlink_gdb_server(str(self.tmp))

        self.assertEqual(Path(found), exe)

    def test_missing_override_raises(self):
        with self.assertRaises(ToolNotFoundError):
            probe_server.find_openocd(str(self.tmp / "missing"))

    def test_path_lookup(self):
        with mock.patch.object(probe_server.shutil, "which", return_value="/usr/bin/openocd"):
            self.assertEqual(probe_server.find_openocd(), "/usr/bin/openocd")

    def test_install_dir_fallback(self):
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(probe_server.shutil, "which", return_value=None),
            mock.patch.object(probe_server, "_jlink_install_dirs", return_value=[self.tmp]),
        ):
            exe = self._make_exe(self.tmp / "JLinkGDBServerCLExe")

            found = probe_server.find_jlink_gdb_server()

        self.assertEqual(Path(found), exe)

    def test_not_found_raises_with_hint(self):
        with (
            mock.patch.object(probe_server.shutil, "which", return_value=None),
            mock.patch.object(probe_server, "_jlink_install_dirs", return_value=[]),
            self.assertRaises(ToolNotFoundError) as ctx,
        ):
            probe_server.find_jlink_gdb_server()

        self.assertIn("J-Link Software Pack", str(ctx.exception))

    def test_windows_install_dirs_prefer_unversioned_then_newest(self):
        for name in ("JLink_V794", "JLink_V810a", "JLink"):
            (self.tmp / "SEGGER" / name).mkdir(parents=True)
        env = {"ProgramFiles": str(self.tmp), "ProgramFiles(x86)": str(self.tmp / "none")}

        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.dict(probe_server.os.environ, env),
        ):
            dirs = probe_server._jlink_install_dirs()

        self.assertEqual([d.name for d in dirs], ["JLink", "JLink_V810a", "JLink_V794"])

    def test_stlink_install_dir_fallback(self):
        bin_dir = self.tmp / "STM32CubeCLT_1.16.0" / "STLink-gdb-server" / "bin"
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(probe_server.shutil, "which", return_value=None),
            mock.patch.object(probe_server, "_st_roots", return_value=[self.tmp]),
        ):
            exe = self._make_exe(bin_dir / "ST-LINK_gdbserver")

            found = probe_server.find_stlink_gdb_server()

        self.assertEqual(Path(found), exe)

    def test_stlink_not_found_raises_with_hint(self):
        with (
            mock.patch.object(probe_server.shutil, "which", return_value=None),
            mock.patch.object(probe_server, "_st_roots", return_value=[self.tmp]),
            self.assertRaises(ToolNotFoundError) as ctx,
        ):
            probe_server.find_stlink_gdb_server()

        self.assertIn("STM32CubeCLT", str(ctx.exception))

    def test_cubeprogrammer_found_next_to_cubeclt_gdbserver(self):
        clt = self.tmp / "STM32CubeCLT"
        with mock.patch.object(sys, "platform", "linux"):
            server = self._make_exe(clt / "STLink-gdb-server" / "bin" / "ST-LINK_gdbserver")
            self._make_exe(clt / "STM32CubeProgrammer" / "bin" / "STM32_Programmer_CLI")

            found = probe_server.find_stm32cubeprogrammer(None, str(server))

        self.assertEqual(Path(found), (clt / "STM32CubeProgrammer" / "bin").resolve())

    def test_cubeprogrammer_found_in_cubeide_plugins(self):
        plugins = self.tmp / "plugins"
        stlink = "com.st.stm32cube.ide.mcu.externaltools.stlink-gdb-server.linux64_2.1.0"
        cubeprog = "com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.linux64_2.1.0"
        with mock.patch.object(sys, "platform", "linux"):
            server = self._make_exe(plugins / stlink / "tools" / "bin" / "ST-LINK_gdbserver")
            self._make_exe(plugins / cubeprog / "tools" / "bin" / "STM32_Programmer_CLI")

            found = probe_server.find_stm32cubeprogrammer(None, str(server))

        self.assertEqual(Path(found).resolve(), (plugins / cubeprog / "tools" / "bin").resolve())

    def test_cubeprogrammer_override_folder_or_file(self):
        with mock.patch.object(sys, "platform", "linux"):
            cli = self._make_exe(self.tmp / "bin" / "STM32_Programmer_CLI")

            self.assertEqual(
                probe_server.find_stm32cubeprogrammer(str(cli.parent)), str(cli.parent)
            )
            self.assertEqual(probe_server.find_stm32cubeprogrammer(str(cli)), str(cli.parent))

    def test_cubeprogrammer_missing_raises(self):
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(probe_server.shutil, "which", return_value=None),
            mock.patch.object(probe_server, "_cubeprogrammer_install_dirs", return_value=[]),
            self.assertRaises(ToolNotFoundError),
        ):
            probe_server.find_stm32cubeprogrammer(None, str(self.tmp / "a" / "b" / "st"))

        with self.assertRaises(ToolNotFoundError):
            probe_server.find_stm32cubeprogrammer(str(self.tmp / "missing"))

    def test_windows_executable_names(self):
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(probe_server.shutil, "which", return_value=None),
            mock.patch.object(probe_server, "_jlink_install_dirs", return_value=[]),
            self.assertRaises(ToolNotFoundError) as ctx,
        ):
            probe_server.find_jlink_gdb_server()

        self.assertIn("JLinkGDBServerCL.exe", str(ctx.exception))


class TestDebugProbeServer(unittest.IsolatedAsyncioTestCase):
    async def test_stlink_ready_line_without_telnet_port(self):
        probed = []

        async def port_probe(host, port):
            probed.append(port)
            return False

        proc = FakeProcess(
            ["STMicroelectronics ST-LINK GDB server", "Waiting for debugger connection..."]
        )
        server = make_server(stlink_config(), proc, port_probe=port_probe)

        with mock.patch.object(probe_server, "find_stm32cubeprogrammer", return_value="/cp"):
            await server.start()

        self.assertTrue(server.is_running)
        self.assertEqual(probed, [61234])
        await server.stop()

    async def test_stlink_device_is_verified_before_launch(self):
        calls = []
        verified = []

        def verifier(cfg, executable):
            verified.append((cfg.device, executable))
            return "STM32F446xC/E (ID 0x421)"

        server = make_server(
            stlink_config(device="STM32F446RE"),
            FakeProcess(["Waiting for debugger connection..."]),
            calls,
            target_verifier=verifier,
        )

        with mock.patch.object(probe_server, "find_stm32cubeprogrammer", return_value="/cp"):
            await server.start()

        self.assertEqual(verified, [("STM32F446RE", "/usr/bin/tool")])
        self.assertEqual(len(calls), 1)
        await server.stop()

    async def test_stlink_device_and_under_reset_verifies_then_launches_with_k(self):
        calls = []
        modes = []

        def verifier(cfg, executable):
            modes.append(cfg.connect_mode)
            return "STM32WBA52/54/55 (ID 0x492)"

        server = make_server(
            stlink_config(device="STM32WBA55CG", connect_mode="under-reset"),
            FakeProcess(["Waiting for debugger connection..."]),
            calls,
            target_verifier=verifier,
        )

        with mock.patch.object(probe_server, "find_stm32cubeprogrammer", return_value="/cp"):
            await server.start()

        self.assertEqual(modes, ["under-reset"])
        self.assertIn("-k", calls[0][0])
        await server.stop()

    async def test_stlink_wrong_target_never_starts_gdb_server(self):
        calls = []

        def verifier(cfg, executable):
            raise HardwareUnavailableError("ST-LINK target is STM32L476xx, not STM32F446RE")

        server = make_server(
            stlink_config(device="STM32F446RE"), FakeProcess(), calls, target_verifier=verifier
        )

        with (
            mock.patch.object(probe_server, "find_stm32cubeprogrammer", return_value="/cp"),
            self.assertRaisesRegex(HardwareUnavailableError, "STM32L476xx"),
        ):
            await server.start()

        self.assertEqual(calls, [])
        self.assertFalse(server.is_running)

    async def test_stlink_without_device_skips_verification(self):
        def verifier(cfg, executable):
            raise AssertionError("must not verify")

        server = make_server(
            stlink_config(),
            FakeProcess(["Waiting for debugger connection..."]),
            target_verifier=verifier,
        )

        with mock.patch.object(probe_server, "find_stm32cubeprogrammer", return_value="/cp"):
            await server.start()

        self.assertTrue(server.is_running)
        await server.stop()

    async def test_stlink_non_st_device_is_rejected_at_start(self):
        server = make_server(stlink_config(device="LPC1768"), FakeProcess())

        with (
            mock.patch.object(probe_server, "find_stm32cubeprogrammer", return_value="/cp"),
            self.assertRaisesRegex(HardwareUnavailableError, "STM32 devices only"),
        ):
            await server.start()

    async def test_stlink_accepts_alternative_ready_lines(self):
        for line in ("Waiting for connection on port 61234...", "Listening at *:61234..."):
            with self.subTest(line=line):
                server = make_server(stlink_config(), FakeProcess([line]))

                with mock.patch.object(
                    probe_server, "find_stm32cubeprogrammer", return_value="/cp"
                ):
                    await server.start()

                self.assertTrue(server.is_running)
                await server.stop()

    async def test_stlink_warns_that_bind_is_not_applied(self):
        server = make_server(stlink_config(), FakeProcess(["Waiting for debugger connection..."]))

        with (
            mock.patch.object(probe_server, "find_stm32cubeprogrammer", return_value="/cp"),
            self.assertLogs(probe_server.logger, level="WARNING") as logs,
        ):
            await server.start()

        self.assertIn("no bind option", logs.output[0])
        await server.stop()

    async def test_start_waits_for_ready_line_and_logs_output(self):
        proc = FakeProcess(
            [
                "Open On-Chip Debugger 0.12.0",
                "Warn : slow",
                "Listening on port 3333 for gdb connections",
            ]
        )
        calls = []
        server = make_server(openocd_config(), proc, calls)

        with self.assertLogs("portbridge.probe.openocd", level="INFO") as logs:
            await server.start()

        self.assertTrue(server.is_running)
        argv, kwargs = calls[0]
        self.assertEqual(argv[0], "/usr/bin/tool")
        self.assertIn("gdb_port 3333", argv)
        self.assertEqual(kwargs["stdout"], asyncio.subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], asyncio.subprocess.STDOUT)
        self.assertEqual(kwargs["stdin"], asyncio.subprocess.DEVNULL)
        self.assertIn("WARNING:portbridge.probe.openocd.3333:Warn : slow", logs.output)

        await server.stop()

        self.assertTrue(proc.terminated)
        self.assertFalse(proc.killed)
        self.assertFalse(server.is_running)

    async def test_start_ready_when_telnet_port_opens(self):
        proc = FakeProcess()
        launched = []

        async def port_probe(_host, _port):
            return bool(launched)

        server = DebugProbeServer(
            jlink_config(),
            process_factory=make_factory(proc, launched),
            port_probe=port_probe,
            tool_finder=lambda _cfg: "jl",
            poll_interval=0.01,
        )

        await server.start()

        self.assertTrue(server.is_running)
        await server.stop()

    async def test_early_exit_reports_output_tail(self):
        proc = FakeProcess(["Error: unable to find ICDI"], exit_code=1)
        server = make_server(openocd_config(), proc)

        with (
            self.assertLogs("portbridge.probe.openocd", level="ERROR"),
            self.assertRaises(HardwareUnavailableError) as ctx,
        ):
            await server.start()

        self.assertIn("code 1", str(ctx.exception))
        self.assertIn("unable to find ICDI", str(ctx.exception))
        self.assertFalse(server.is_running)

    async def test_stlink_not_halted_suggests_connect_under_reset(self):
        lines = ["Target not halted after reset. Force halt", "Failed to halt target"]

        for mode, hinted in (("normal", True), ("hotplug", True), ("under-reset", False)):
            with self.subTest(mode=mode):
                server = make_server(
                    stlink_config(connect_mode=mode), FakeProcess(lines, exit_code=9)
                )

                with (
                    mock.patch.object(probe_server, "find_stm32cubeprogrammer", return_value="/cp"),
                    self.assertLogs("portbridge.probe_server", level="WARNING"),
                    self.assertRaises(HardwareUnavailableError) as ctx,
                ):
                    await server.start()

                self.assertIn("Failed to halt target", str(ctx.exception))
                self.assertEqual("'under-reset'" in str(ctx.exception), hinted)

    async def test_stlink_not_halted_suggests_access_port_only_when_unset(self):
        lines = ["Failed to halt target"]
        cases = (
            (stlink_config(connect_mode="under-reset"), True),
            (stlink_config(connect_mode="under-reset", device="STM32WBA55CG"), False),
            (stlink_config(connect_mode="under-reset", access_port=0), False),
        )
        for cfg, hinted in cases:
            with self.subTest(device=cfg.device, access_port=cfg.access_port):
                server = make_server(cfg, FakeProcess(lines, exit_code=9))

                with (
                    mock.patch.object(probe_server, "find_stm32cubeprogrammer", return_value="/cp"),
                    self.assertLogs("portbridge.probe_server", level="WARNING"),
                    self.assertRaises(HardwareUnavailableError) as ctx,
                ):
                    await server.start()

                self.assertEqual("access port" in str(ctx.exception), hinted)

    async def test_startup_timeout_terminates_process(self):
        proc = FakeProcess(["Connecting to target..."])
        server = make_server(openocd_config(), proc, startup_timeout=0.05)

        with self.assertRaises(HardwareUnavailableError) as ctx:
            await server.start()

        self.assertIn("did not become ready", str(ctx.exception))
        self.assertTrue(proc.terminated)

    async def test_port_in_use_is_reported_before_launch(self):
        calls = []

        async def port_open(_host, _port):
            return True

        server = make_server(openocd_config(), FakeProcess(), calls, port_probe=port_open)

        with self.assertRaises(PortUnavailableError):
            await server.start()

        self.assertEqual(calls, [])

    async def test_rtt_port_in_use_is_reported_before_launch(self):
        calls = []
        probed = []

        async def only_rtt_open(_host, port):
            probed.append(port)
            return port == 19021

        cfg = openocd_config(rtt_port=19021)
        server = make_server(cfg, FakeProcess(), calls, port_probe=only_rtt_open)

        with self.assertRaisesRegex(PortUnavailableError, "19021"):
            await server.start()

        self.assertEqual(calls, [])
        self.assertEqual(probed, [3333, 4444, 19021])

    async def test_rtt_port_is_logged_when_listening(self):
        proc = FakeProcess(["Listening on port 3333 for gdb connections"])
        server = make_server(openocd_config(rtt_port=19021), proc)

        with self.assertLogs(probe_server.logger, level="INFO") as logs:
            await server.start()

        self.assertTrue(any("RTT port 19021" in line for line in logs.output))
        await server.stop()

    async def test_stlink_rtt_is_refused_before_launch(self):
        calls = []
        server = make_server(stlink_config(rtt_port=19021), FakeProcess(), calls)

        with (
            mock.patch.object(probe_server, "find_stm32cubeprogrammer", return_value="/cp"),
            self.assertRaisesRegex(HardwareUnavailableError, "interface/stlink.cfg"),
        ):
            await server.start()

        self.assertEqual(calls, [])

    async def test_jlink_rtt_on_all_interfaces_warns_about_old_software(self):
        proc = FakeProcess(["Waiting for GDB connection..."])
        cfg = jlink_config(rtt_port=19021, bind_address="0.0.0.0")
        server = make_server(cfg, proc)

        with self.assertLogs(probe_server.logger, level="WARNING") as logs:
            await server.start()

        self.assertTrue(any("V7.80c" in line for line in logs.output))
        await server.stop()

    async def test_jlink_rtt_on_loopback_does_not_warn(self):
        proc = FakeProcess(["Waiting for GDB connection..."])
        server = make_server(jlink_config(rtt_port=19021), proc)

        with self.assertNoLogs(probe_server.logger, level="WARNING"):
            await server.start()

        await server.stop()

    async def test_launch_failure_is_hardware_unavailable(self):
        async def failing_factory(*_argv, **_kwargs):
            raise PermissionError("denied")

        server = DebugProbeServer(
            openocd_config(),
            process_factory=failing_factory,
            port_probe=port_closed,
            tool_finder=lambda _cfg: "openocd",
        )

        with self.assertRaises(HardwareUnavailableError):
            await server.start()

    async def test_invalid_config_is_hardware_unavailable(self):
        calls = []
        server = make_server(jlink_config(device=None), FakeProcess(), calls)

        with self.assertRaises(HardwareUnavailableError):
            await server.start()

        self.assertEqual(calls, [])

    async def test_missing_tool_propagates_tool_not_found(self):
        def finder(_cfg):
            raise ToolNotFoundError("no openocd")

        server = DebugProbeServer(openocd_config(), tool_finder=finder)

        with self.assertRaises(ToolNotFoundError):
            await server.start()

    async def test_stop_kills_process_that_ignores_terminate(self):
        proc = FakeProcess(["Listening on port 3333 for gdb connections"], ignore_terminate=True)
        server = make_server(openocd_config(), proc, stop_timeout=0.05)
        await server.start()

        with self.assertLogs(probe_server.logger, level="WARNING"):
            await server.stop()

        self.assertTrue(proc.terminated)
        self.assertTrue(proc.killed)

    async def test_unexpected_exit_after_start_is_logged(self):
        proc = FakeProcess(["Listening on port 3333 for gdb connections"])
        server = make_server(openocd_config(), proc)
        await server.start()

        with self.assertLogs(probe_server.logger, level="ERROR") as logs:
            proc.finish(3)
            await server._exited.wait()
            await asyncio.sleep(0)

        self.assertIn("exited unexpectedly with code 3", logs.output[0])
        self.assertFalse(server.is_running)
        await server.stop()

    async def test_cancelled_start_terminates_process(self):
        proc = FakeProcess()
        server = make_server(openocd_config(), proc)

        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.05)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(proc.terminated)

    async def test_stop_without_start_is_noop(self):
        server = make_server(openocd_config(), FakeProcess())

        await server.stop()


class TestProbeDiscovery(unittest.TestCase):
    EMU_LIST = (
        "SEGGER J-Link Commander V7.94\n"
        "J-Link>ShowEmuList\n"
        "J-Link[0]: Connection: USB, Serial number: 801012345, ProductName: J-Link EDU\n"
        "J-Link[1]: Connection: USB, Serial number: 69000001, ProductName: J-Link PLUS\n"
        "J-Link>exit\n"
    )

    def test_parse_jlink_emu_list(self):
        probes = probe_server.parse_jlink_emu_list(self.EMU_LIST)

        self.assertEqual(
            probes,
            [
                {
                    "probe": "jlink",
                    "serial": "801012345",
                    "details": "J-Link EDU (USB)",
                    "source": "jlink-commander",
                },
                {
                    "probe": "jlink",
                    "serial": "69000001",
                    "details": "J-Link PLUS (USB)",
                    "source": "jlink-commander",
                },
            ],
        )

    def test_parse_jlink_emu_list_empty(self):
        self.assertEqual(probe_server.parse_jlink_emu_list("J-Link>ShowEmuList\n"), [])

    def test_detect_jlink_probes_runs_commander_script(self):
        completed = subprocess.CompletedProcess([], 0, stdout=self.EMU_LIST, stderr="")
        with (
            mock.patch.object(probe_server, "find_jlink_commander", return_value="JLinkExe"),
            mock.patch.object(probe_server.subprocess, "run", return_value=completed) as run,
        ):
            probes = probe_server.detect_jlink_probes()

        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "JLinkExe")
        self.assertIn("-CommanderScript", argv)
        self.assertEqual(len(probes), 2)

    def test_detect_jlink_probes_without_commander(self):
        with mock.patch.object(
            probe_server, "find_jlink_commander", side_effect=ToolNotFoundError("x")
        ):
            self.assertEqual(probe_server.detect_jlink_probes(), [])

    def test_detect_jlink_probes_tolerates_timeout(self):
        with (
            mock.patch.object(probe_server, "find_jlink_commander", return_value="JLinkExe"),
            mock.patch.object(
                probe_server.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("JLinkExe", 15),
            ),
            self.assertLogs(probe_server.logger, level="WARNING"),
        ):
            self.assertEqual(probe_server.detect_jlink_probes(), [])

    def test_detect_openocd_tool_reports_version(self):
        completed = subprocess.CompletedProcess(
            [], 0, stdout="", stderr="Open On-Chip Debugger 0.12.0\nLicensed under GNU GPL v2\n"
        )
        with (
            mock.patch.object(probe_server, "find_openocd", return_value="/usr/bin/openocd"),
            mock.patch.object(probe_server.subprocess, "run", return_value=completed),
        ):
            probes = probe_server.detect_openocd_tool()

        self.assertEqual(len(probes), 1)
        self.assertEqual(probes[0]["probe"], "openocd")
        self.assertIn("0.12.0", probes[0]["details"])
        self.assertIn("/usr/bin/openocd", probes[0]["details"])

    def test_detect_openocd_tool_not_installed(self):
        with mock.patch.object(probe_server, "find_openocd", side_effect=ToolNotFoundError("x")):
            self.assertEqual(probe_server.detect_openocd_tool(), [])

    def test_list_probes_drops_usb_jlinks_when_commander_listed_them(self):
        jlink = {"probe": "jlink", "serial": "1", "details": "", "source": "jlink-commander"}
        usb_jlink = {"probe": "jlink", "serial": "1", "details": "", "source": "usb"}
        icdi = {"probe": "openocd", "serial": "0E1", "details": "ICDI", "source": "usb"}
        with (
            mock.patch.object(probe_server, "detect_jlink_probes", return_value=[jlink]),
            mock.patch.object(probe_server, "detect_usb_probes", return_value=[usb_jlink, icdi]),
            mock.patch.object(probe_server, "detect_openocd_tool", return_value=[]),
            mock.patch.object(probe_server, "detect_stlink_tool", return_value=[]),
        ):
            self.assertEqual(probe_server.list_probes(), [jlink, icdi])

    def test_detect_stlink_tool(self):
        with mock.patch.object(probe_server, "find_stlink_gdb_server", return_value="/st/gdb"):
            probes = probe_server.detect_stlink_tool()

        self.assertEqual(probes[0]["probe"], "stlink")
        self.assertIn("/st/gdb", probes[0]["details"])

        with mock.patch.object(
            probe_server, "find_stlink_gdb_server", side_effect=ToolNotFoundError("x")
        ):
            self.assertEqual(probe_server.detect_stlink_tool(), [])

    def test_usb_stlink_ids_map_to_stlink_kind(self):
        for pid in (0x3748, 0x374B, 0x374E, 0x374F, 0x3753):
            self.assertEqual(probe_server._USB_PROBES[(0x0483, pid)][0], "stlink")

    def test_format_probe_table(self):
        table = probe_server.format_probe_table(
            [{"probe": "openocd", "serial": "0E1", "details": "ICDI", "source": "usb"}]
        )

        self.assertIn("Probe", table.splitlines()[0])
        self.assertIn("ICDI", table)

    def test_format_probe_table_empty(self):
        self.assertIn("No debug probes", probe_server.format_probe_table([]))


if __name__ == "__main__":
    unittest.main()
