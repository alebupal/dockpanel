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
