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
        self.assertIn("WARNING:portbridge.probe.openocd:Warn : slow", logs.output)

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
        ):
            self.assertEqual(probe_server.list_probes(), [jlink, icdi])

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
