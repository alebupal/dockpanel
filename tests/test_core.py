import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DOCKER_DIR", "/srv/docker")

from app import host  # noqa: E402
from app.actions import ActionError, build_plan, parse_check, parse_list  # noqa: E402
from app.sessions import SessionManager  # noqa: E402

NAMES = {"portainer": "Portainer", "it_tools": "IT Tools", "dockpanel": "Dockpanel"}


class BuildPlanTests(unittest.TestCase):
    def test_update_and_force(self):
        self.assertEqual(build_plan({"action": "update", "service": "portainer"}, NAMES).args, ["portainer"])
        plan = build_plan({"action": "update", "service": "portainer", "force": True}, NAMES)
        self.assertEqual(plan.args, ["portainer", "-f"])
        self.assertTrue(plan.modifies)

    def test_lifecycle_all(self):
        plan = build_plan({"action": "stop", "service": "all"}, NAMES)
        self.assertEqual(plan.args, ["--stop", "all"])
        self.assertIn("todos", plan.title)

    def test_unknown_or_malicious_service(self):
        for bad in ("nope", "../etc", "-a", "a b", "x;rm -rf /", None, 3):
            with self.assertRaises(ActionError):
                build_plan({"action": "restart", "service": bad}, NAMES)

    def test_policy_validation(self):
        plan = build_plan({"action": "policy", "service": "it_tools", "policy": "always"}, NAMES)
        self.assertEqual(plan.args, ["--restart-policy", "it_tools", "always"])
        with self.assertRaises(ActionError):
            build_plan({"action": "policy", "service": "it_tools", "policy": "sometimes"}, NAMES)

    def test_update_list_uses_positional_args(self):
        plan = build_plan({"action": "update_list", "services": ["portainer", "it_tools"]}, NAMES)
        self.assertEqual(plan.args, ["update", "-f", "portainer", "it_tools"])
        self.assertIn('"$@"', plan.script)
        with self.assertRaises(ActionError):
            build_plan({"action": "update_list", "services": []}, NAMES)

    def test_batch(self):
        plan = build_plan({"action": "batch", "op": "restart", "services": ["portainer", "it_tools", "portainer"]}, NAMES)
        self.assertEqual(plan.args, ["restart", "", "portainer", "it_tools"])  # sin duplicados
        self.assertIsNotNone(plan.script)
        plan = build_plan({"action": "batch", "op": "update", "force": True, "services": ["it_tools"]}, NAMES)
        self.assertEqual(plan.args[:2], ["update", "-f"])
        plan = build_plan({"action": "batch", "op": "policy", "policy": "no", "services": ["it_tools"]}, NAMES)
        self.assertEqual(plan.args[:2], ["policy", "no"])
        for bad in ({"op": "rm", "services": ["it_tools"]}, {"op": "start", "services": []},
                    {"op": "start", "services": ["nope"]}, {"op": "policy", "policy": "x", "services": ["it_tools"]}):
            with self.assertRaises(ActionError):
                build_plan({"action": "batch", **bad}, NAMES)

    def test_show_one_selected_or_all(self):
        self.assertEqual(build_plan({"action": "show"}, NAMES).args, ["--show"])
        self.assertEqual(build_plan({"action": "show", "services": []}, NAMES).args, ["--show"])
        plan = build_plan({"action": "show", "services": ["portainer", "it_tools"]}, NAMES)
        self.assertEqual(plan.args, ["--show", "portainer", "it_tools"])
        self.assertFalse(plan.modifies)
        with self.assertRaises(ActionError):
            build_plan({"action": "show", "services": ["../x"]}, NAMES)

    def test_readonly_actions(self):
        for action in ("logs", "check", "stats", "show", "help"):
            body = {"action": action, "service": "portainer"}
            self.assertFalse(build_plan(body, NAMES).modifies, action)

    def test_new_and_user_home(self):
        self.assertEqual(build_plan({"action": "new"}, {}).args, ["--new"])
        cmd = "docker run -d -p 8080:80 --name x nginx"
        self.assertEqual(build_plan({"action": "new", "run_cmd": cmd}, {}).args, ["--new", cmd])
        with self.assertRaises(ActionError):
            build_plan({"action": "new", "run_cmd": "a\nb"}, {})
        self.assertEqual(build_plan({"action": "user_home", "path": "/home/u"}, {}).args,
                         ["--set-user-home", "/home/u"])
        with self.assertRaises(ActionError):
            build_plan({"action": "user_home", "path": "relativa"}, {})

    def test_unknown_action(self):
        with self.assertRaises(ActionError):
            build_plan({"action": "rm"}, NAMES)


class ParseTests(unittest.TestCase):
    def test_parse_list(self):
        tsv = ("portainer\tPortainer\trun\t9000\tRUNNING\tportainer/portainer-ce:latest\tportainer\tfalse\n"
               "termix\tTermix\tcompose\t-\tOFFLINE\tghcr.io/x:latest\t\ttrue\n"
               "aviso de bash sin tabuladores\n")
        items = parse_list(tsv)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["port"], "9000")
        self.assertEqual(items[1]["port"], "")
        self.assertTrue(items[1]["backup"])

    def test_parse_check(self):
        self.assertEqual(parse_check("a\tupdate\nb\tok\nc\tweird\n"), {"a": "update", "b": "ok"})


class HostTests(unittest.TestCase):
    def test_job_container_argv(self):
        argv = host.job_container_argv("abc", "Actualizar X", "sha256:1", ["/bin/bash", "/srv/docker/update.sh", "x"])
        self.assertEqual(argv[:2], ["chroot", host.HOST_ROOT])
        i = argv.index("run")
        self.assertIn("--rm", argv[i:])
        self.assertIn("dockpanel-job-abc", argv)
        self.assertIn("sha256:1", argv)
        self.assertEqual(argv[-3:], ["/bin/bash", "/srv/docker/update.sh", "x"])

    def test_missing_update_sh_features(self):
        import tempfile

        with tempfile.TemporaryDirectory() as root:
            os.makedirs(root + host.DOCKER_DIR)
            path = root + host.update_sh_path()
            with mock.patch.object(host, "HOST_ROOT", root):
                self.assertIsNone(host.missing_update_sh_features())  # sin fichero
                with open(path, "w") as fh:
                    fh.write("list_services_tsv() {}\n")
                self.assertEqual(len(host.missing_update_sh_features()), 2)
                with open(path, "w") as fh:
                    fh.write("\n".join(m for m, _ in host.UPDATE_SH_FEATURES))
                self.assertEqual(host.missing_update_sh_features(), [])

    def test_env_passthrough(self):
        with mock.patch.dict(os.environ, {"BACKUP_KEEP_MIN": "5"}):
            self.assertIn("BACKUP_KEEP_MIN=5", host.host_env())


class CliTests(unittest.TestCase):
    def _run(self, args, image="sha256:img"):
        from app import cli

        with mock.patch.object(cli.host, "detect_self", return_value=("dockpanel", image)), \
                mock.patch.object(cli.os, "execvp") as execvp, \
                mock.patch.object(cli.sys.stdin, "isatty", return_value=False):
            cli.main(args)
        return execvp.call_args[0][1]

    def test_read_only_runs_directly(self):
        cmd = self._run(["-c"])
        self.assertNotIn("docker", cmd[:3])
        self.assertEqual(cmd[-3:], ["/bin/bash", host.update_sh_path(), "-c"])
        self.assertNotIn("run", cmd)

    def test_modifying_uses_helper_without_tty(self):
        cmd = self._run(["-a", "-f"])
        self.assertIn("run", cmd)
        self.assertIn("-i", cmd)
        self.assertNotIn("-it", cmd)
        self.assertEqual(cmd[-2:], ["-a", "-f"])

    def test_menu_without_args_uses_helper(self):
        self.assertIn("sha256:img", self._run([]))


class CheckTests(unittest.TestCase):
    def test_progress_and_partial_merge(self):
        from app import main

        script = 'printf "a\\tok\\n"; sleep 0.2; printf "b\\tupdate\\n"'
        main.state.update(check={"a": "update", "b": "update", "c": "update"}, check_time=1.0)
        with mock.patch.object(main.host, "on_host", return_value=["/bin/sh", "-c", script]):
            asyncio.run(main.run_check(["a", "b"]))
        st = main._check_status()
        self.assertFalse(st["running"])
        self.assertEqual((st["done"], st["total"], st["scope"]), (2, 2, "partial"))
        # Parcial: solo cambian los recomprobados y no se toca la hora de la comprobación completa
        self.assertEqual(st["check"], {"a": "ok", "b": "update", "c": "update"})
        self.assertEqual(st["check_time"], 1.0)

    def test_recheck_after_modifying_task(self):
        from app import main

        main.state.update(check={"a": "update", "b": "ok"})
        calls = []

        async def fake_check(services=None):
            calls.append(services)

        async def scenario():
            with mock.patch.object(main, "run_check", fake_check):
                main._on_session_finished(mock.Mock(container="dockpanel-job-x"))
                main._on_session_finished(mock.Mock(container=""))  # consulta: no recomprueba
                await asyncio.sleep(0)

        asyncio.run(scenario())
        self.assertEqual(calls, [["a"]])


class CheckApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from app import main

        self.main = main
        self.client = TestClient(main.app)
        self.calls = []

        async def fake_check(services=None, source="manual"):
            self.calls.append(services)

        async def fake_list():
            return [{"folder": "portainer"}, {"folder": "immich-app"}]

        self.patches = [
            mock.patch.object(main, "run_check", fake_check),
            mock.patch.object(main, "_list_services", fake_list),
            mock.patch.object(main.host, "DOCKER_DIR", "/srv/docker"),
        ]
        for p in self.patches:
            p.start()
        main.state["check_running"] = False

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def post(self, body):
        return self.client.post("/api/check", json=body, headers={"X-Dockpanel": "1"})

    def test_single_service(self):
        self.assertEqual(self.post({"services": ["portainer"]}).status_code, 200)
        self.assertEqual(self.calls, [["portainer"]])

    def test_all_without_services(self):
        self.assertEqual(self.post({}).status_code, 200)
        self.assertEqual(self.calls, [None])

    def test_unknown_service_rejected(self):
        res = self.post({"services": ["portainer", "nope"]})
        self.assertEqual(res.status_code, 400)
        self.assertIn("nope", res.json()["detail"])
        self.assertEqual(self.calls, [])

    def test_single_service_while_running_is_queued(self):
        self.main.state["check_running"] = True
        self.assertEqual(self.post({"services": ["immich-app"]}).status_code, 200)
        self.assertEqual(self.calls, [["immich-app"]])  # run_check lo encola


class CancelAndAutoTests(unittest.TestCase):
    def test_cancel_kills_running_check(self):
        from app import main

        script = 'printf "a\\tok\\n"; sleep 30; printf "b\\tupdate\\n"'
        main.state.update(check={}, check_time=5.0, check_queue=None)

        async def scenario():
            with mock.patch.object(main.host, "on_host", return_value=["/bin/sh", "-c", script]):
                task = asyncio.create_task(main.run_check(["a", "b"]))
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    if main.state["check_done"]:
                        break
                main.state["check_queue"] = "all"  # una encolada detrás también se descarta
                await main.cancel_check()
                await asyncio.wait_for(task, 5)

        asyncio.run(scenario())
        st = main._check_status()
        self.assertFalse(st["running"])
        self.assertTrue(st["cancelled"])
        self.assertIsNone(st["error"])
        self.assertEqual(st["check"], {"a": "ok"})  # lo que ya llegó se conserva
        self.assertEqual(st["check_time"], 5.0)
        self.assertIsNone(main.state["check_queue"])

    def test_auto_toggle_persists_and_skips_recheck(self):
        import tempfile

        from fastapi.testclient import TestClient

        from app import main

        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(main.host, "HOST_ROOT", root), \
                    mock.patch.object(main.host, "DOCKER_DIR", "/srv/docker"):
                client = TestClient(main.app)
                res = client.put("/api/check/auto", json={"enabled": False}, headers={"X-Dockpanel": "1"})
                self.assertEqual(res.status_code, 200)
                self.assertFalse(res.json()["auto_check"])
                self.assertIsNone(res.json()["check_next"])
                self.assertEqual(main.load_settings(), {"auto_check": False})
                bad = client.put("/api/check/auto", json={"enabled": "no"}, headers={"X-Dockpanel": "1"})
                self.assertEqual(bad.status_code, 400)

                calls = []

                async def fake_check(services=None):
                    calls.append(services)

                async def scenario():
                    main.state["check"] = {"a": "update"}
                    with mock.patch.object(main, "run_check", fake_check):
                        main._on_session_finished(mock.Mock(container="dockpanel-job-x"))
                        await asyncio.sleep(0)

                asyncio.run(scenario())
                self.assertEqual(calls, [])
                main.state["auto_check"] = True


class IndexTests(unittest.TestCase):
    def test_static_urls_get_content_hash(self):
        from app import main

        html = main.render_index()
        self.assertRegex(html, r"/static/app\.js\?v=[0-9a-f]{10}")
        self.assertRegex(html, r"/static/style\.css\?v=[0-9a-f]{10}")
        self.assertNotIn("?v=10", html)


class ScheduleTests(unittest.TestCase):
    def test_parse_check_times(self):
        from app.main import parse_check_times

        self.assertEqual(parse_check_times("08:00"), [(8, 0)])
        self.assertEqual(parse_check_times("20:30, 8:05;8:05"), [(8, 5), (20, 30)])
        self.assertEqual(parse_check_times("7"), [(7, 0)])
        for off in ("", "off", "25:00", "aa:bb"):
            self.assertEqual(parse_check_times(off), [], off)

    def test_next_check_at(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from app.main import next_check_at

        tz = ZoneInfo("Europe/Madrid")
        times = [(8, 0), (20, 30)]
        self.assertEqual(next_check_at(datetime(2026, 11, 12, 7, 59, tzinfo=tz), times),
                         datetime(2026, 11, 12, 8, 0, tzinfo=tz))
        self.assertEqual(next_check_at(datetime(2026, 11, 12, 8, 0, tzinfo=tz), times),
                         datetime(2026, 11, 12, 20, 30, tzinfo=tz))
        self.assertEqual(next_check_at(datetime(2026, 11, 12, 21, 0, tzinfo=tz), times),
                         datetime(2026, 11, 13, 8, 0, tzinfo=tz))
        self.assertIsNone(next_check_at(datetime(2026, 11, 12, 21, 0, tzinfo=tz), []))


class PortLinkTests(unittest.TestCase):
    def test_override_wins(self):
        from app import main
        with mock.patch.object(main, "PORT_LINK_HOST", "192.168.5.10"):
            self.assertEqual(main.port_link_host(), "192.168.5.10")

    def test_autodetect_ignores_loopback(self):
        from app import main
        ip = main.detect_lan_ip()
        self.assertFalse(ip.startswith("127."))


class SessionTests(unittest.TestCase):
    def test_pty_session_output_and_exit(self):
        async def scenario():
            mgr = SessionManager()
            s = mgr.create("prueba", ["/bin/sh", "-c", 'test -t 1 && echo "tty ok"; exit 3'])
            q = s.subscribe()
            out = b""
            code = None
            while code is None:
                item = await asyncio.wait_for(q.get(), 5)
                if isinstance(item, bytes):
                    out += item
                elif item.get("t") == "exit":
                    code = item["code"]
            return out, code, mgr, s

        out, code, mgr, s = asyncio.run(scenario())
        self.assertIn(b"tty ok", out)
        self.assertEqual(code, 3)
        self.assertTrue(mgr.remove(s.id))

    def test_input_reaches_process(self):
        async def scenario():
            mgr = SessionManager()
            s = mgr.create("eco", ["/bin/sh", "-c", 'read x; echo "got:$x"'])
            q = s.subscribe()
            s.write("hola\r")
            out = b""
            while True:
                item = await asyncio.wait_for(q.get(), 5)
                if isinstance(item, bytes):
                    out += item
                else:
                    return out

        self.assertIn(b"got:hola", asyncio.run(scenario()))


class ApiSecurityTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from app import main

        self.main = main
        self.client = TestClient(main.app)

    def test_post_requires_custom_header(self):
        res = self.client.post("/api/sessions", json={"action": "help"})
        self.assertEqual(res.status_code, 403)

    def test_basic_auth(self):
        with mock.patch.object(self.main, "AUTH_USER", "admin"), mock.patch.object(self.main, "AUTH_PASSWORD", "x"):
            self.assertEqual(self.client.get("/api/info").status_code, 401)
            self.assertEqual(self.client.get("/api/info", auth=("admin", "x")).status_code, 200)
            self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_same_origin(self):
        self.assertTrue(self.main._same_origin("http://nas:8092", "nas:8092"))
        self.assertFalse(self.main._same_origin("http://evil.example", "nas:8092"))
        self.assertTrue(self.main._same_origin(None, "nas:8092"))


if __name__ == "__main__":
    unittest.main()
