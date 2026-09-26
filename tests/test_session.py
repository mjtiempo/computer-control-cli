"""Tests for the session: environment resolution and hyprctl access."""

import json
import os
import tempfile
import unittest

from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import ProcessRunner, Session  # pi-lens-ignore: reportMissingImports

from tests.fakes import FakeDiscoverer, FakeRunner  # pi-lens-ignore: reportMissingImports

ENV = {"HYPRLAND_INSTANCE_SIGNATURE": "sig_1",
       "WAYLAND_DISPLAY": "wayland-1",
       "XDG_RUNTIME_DIR": "/run/user/1000"}

MONITORS = [
    {"name": "eDP-1", "x": 0, "y": 0, "width": 1920, "height": 1080,
     "scale": 1.0, "focused": True},
    {"name": "HDMI-A-1", "x": 1920, "y": 0, "width": 2560, "height": 1440,
     "scale": 1.0, "focused": False},
]


class SessionTests(unittest.TestCase):
    def make(self, runner=None, env=ENV, discovery=ENV):
        return Session(env=env, runner=runner or FakeRunner(),
                       discoverer=FakeDiscoverer(discovery))

    def test_env_returns_the_injected_environment(self):
        self.assertEqual(self.make().env()["WAYLAND_DISPLAY"], "wayland-1")

    def test_missing_session_refuses(self):
        session = self.make(env=None, discovery=None)

        with self.assertRaises(ControlError) as caught:
            session.env()

        self.assertEqual(caught.exception.code, "no-session")

    def test_discovered_environment_is_cached(self):
        session = self.make(env=None)

        self.assertEqual(session.env(), ENV)

    def test_run_passes_the_session_environment(self):
        runner = FakeRunner(responses={"hyprctl cursorpos": "5,6"})
        session = self.make(runner=runner)

        session.run(["hyprctl", "cursorpos"])

        self.assertEqual(runner.environments[-1]["WAYLAND_DISPLAY"], "wayland-1")

    def test_missing_binary_is_not_found(self):
        runner = FakeRunner()
        runner.missing.add("hyprctl -j monitors")
        session = self.make(runner=runner)

        with self.assertRaises(ControlError) as caught:
            session.hypr_json(["-j", "monitors"])

        self.assertEqual(caught.exception.code, "not-found")

    def test_wedged_command_is_a_timeout_refusal(self):
        runner = FakeRunner()
        runner.timeouts.add("hyprctl -j monitors")
        session = self.make(runner=runner)

        with self.assertRaises(ControlError) as caught:
            session.hypr_json(["-j", "monitors"])

        self.assertEqual(caught.exception.code, "subprocess-timeout")

    def test_hyprctl_failure_is_a_hyprctl_refusal(self):
        runner = FakeRunner(responses={"hyprctl -j clients": "boom"},
                            returncodes={"hyprctl -j clients": 1})
        session = self.make(runner=runner)

        with self.assertRaises(ControlError) as caught:
            session.hypr_json(["-j", "clients"])

        self.assertEqual(caught.exception.code, "hyprctl")

    def test_bad_json_is_a_refusal(self):
        runner = FakeRunner(responses={"hyprctl -j clients": "{not json"})
        session = self.make(runner=runner)

        with self.assertRaises(ControlError) as caught:
            session.hypr_json(["-j", "clients"])

        self.assertEqual(caught.exception.code, "hyprctl")

    def test_dispatch_accepts_ok(self):
        runner = FakeRunner(responses={"hyprctl dispatch x": "ok"})
        session = self.make(runner=runner)

        session.dispatch("x")  # must not raise

    def test_dispatch_refuses_a_non_ok_reply(self):
        runner = FakeRunner(responses={"hyprctl dispatch x": "error: nope"})
        session = self.make(runner=runner)

        with self.assertRaises(ControlError) as caught:
            session.dispatch("x")

        self.assertEqual(caught.exception.code, "dispatch")

    def test_monitors_are_logical_rects(self):
        runner = FakeRunner(responses={"hyprctl -j monitors": json.dumps(MONITORS)})
        session = self.make(runner=runner)

        monitors = session.monitors()

        self.assertEqual([m.name for m in monitors], ["eDP-1", "HDMI-A-1"])
        self.assertEqual(monitors[0].w, 1920)

    def test_cursor_parses_x_y(self):
        runner = FakeRunner(responses={"hyprctl cursorpos": " 12,34\n"})
        session = self.make(runner=runner)

        self.assertEqual(session.cursor(), (12, 34))

    def test_malformed_cursor_refuses(self):
        runner = FakeRunner(responses={"hyprctl cursorpos": "nope"})
        session = self.make(runner=runner)

        with self.assertRaises(ControlError) as caught:
            session.cursor()

        self.assertEqual(caught.exception.code, "hyprctl")

    def test_desktop_bbox_is_the_union_of_monitors(self):
        runner = FakeRunner(responses={"hyprctl -j monitors": json.dumps(MONITORS)})
        session = self.make(runner=runner)

        self.assertEqual(session.desktop_bbox(), (0, 0, 4480, 1440))

    def test_anchor_monitor_is_the_one_owning_the_origin(self):
        runner = FakeRunner(responses={"hyprctl -j monitors": json.dumps(MONITORS)})
        session = self.make(runner=runner)

        anchor = session.anchor_monitor()
        assert anchor is not None
        self.assertEqual(anchor.name, "eDP-1")

    def test_ydotool_socket_env_override(self):
        socket = Session.ydotool_socket("1000", {"YDOTOOL_SOCKET": "/tmp/x.sock"})

        self.assertEqual(socket, "/tmp/x.sock")

    def test_process_runner_is_the_default(self):
        self.assertIsInstance(ProcessRunner(), ProcessRunner)

    def test_with_env_refuses_a_missing_instance(self):
        env = {**ENV, "XDG_RUNTIME_DIR": tempfile.mkdtemp()}
        session = self.make(env=env, discovery=env)

        with self.assertRaises(ControlError) as caught:
            session.with_env({"HYPRLAND_INSTANCE_SIGNATURE": "nope"})

        self.assertEqual(caught.exception.code, "no-session")

    def test_with_env_overrides_and_keeps_the_devices(self):
        runtime = tempfile.mkdtemp()
        socket_dir = os.path.join(runtime, "hypr", "sig_2")
        os.makedirs(socket_dir)
        open(os.path.join(socket_dir, ".socket.sock"), "w").close()
        env = {**ENV, "XDG_RUNTIME_DIR": runtime}
        runner = FakeRunner(responses={"hyprctl cursorpos": "1,2"})
        session = self.make(runner=runner, env=env, discovery=env)

        other = session.with_env({"HYPRLAND_INSTANCE_SIGNATURE": "sig_2"})
        other.run(["hyprctl", "cursorpos"])

        self.assertEqual(other.env()["HYPRLAND_INSTANCE_SIGNATURE"], "sig_2")
        self.assertEqual(runner.calls[-1][:2], ["hyprctl", "cursorpos"])
        self.assertEqual(
            runner.environments[-1]["HYPRLAND_INSTANCE_SIGNATURE"], "sig_2")


if __name__ == "__main__":
    unittest.main()
