"""PROTOTYPE — throwaway. Run a config inside a nested Hyprland and dump its state.

The host session (still hyprlang, 0.56.2) is never touched: the child gets its own
instance signature and its own wayland socket, exactly like
prototypes/schema-page/nested/start.sh from issue #8.
"""
import json
import os
import shlex
import signal
import subprocess
import time

RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
HOST_DISPLAY = os.environ.get("WAYLAND_DISPLAY", "wayland-1")

# state surfaces the ticket asks to compare
DUMPS = ["binds", "monitors", "workspacerules", "layers", "animations", "devices",
         "clients", "workspaces", "layouts", "configerrors", "globalshortcuts"]


def _instances(env):
    p = subprocess.run(["hyprctl", "instances", "-j"], capture_output=True,
                       text=True, env=env)
    try:
        return {i["instance"]: i for i in json.loads(p.stdout)}
    except Exception:
        return {}


class Nested:
    def __init__(self, config, home=None, log=None, timeout=40):
        self.config = config
        self.home = home
        self.log = log
        self.timeout = timeout
        self.sig = None
        self.display = None
        self.proc = None

    def _base_env(self):
        env = dict(os.environ)
        env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
        env["XDG_RUNTIME_DIR"] = RUNTIME
        env["WAYLAND_DISPLAY"] = HOST_DISPLAY
        if self.home:
            env["HOME"] = self.home
            env["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
            env["XDG_DATA_HOME"] = os.path.join(self.home, ".local", "share")
            env["XDG_STATE_HOME"] = os.path.join(self.home, ".local", "state")
            env["XDG_CACHE_HOME"] = os.path.join(self.home, ".cache")
        return env

    def env(self):
        env = self._base_env()
        env["HYPRLAND_INSTANCE_SIGNATURE"] = self.sig
        env["WAYLAND_DISPLAY"] = self.display
        return env

    def __enter__(self):
        base = self._base_env()
        before = set(_instances(base))
        logf = open(self.log, "w") if self.log else subprocess.DEVNULL
        self.proc = subprocess.Popen(["Hyprland", "-c", self.config], env=base,
                                     stdout=logf, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"Hyprland exited early ({self.proc.returncode}) "
                                   f"for {self.config}")
            now = _instances(base)
            new = [s for s in now if s not in before]
            if new:
                self.sig = new[0]
                self.display = now[self.sig]["wl_socket"]
                break
            time.sleep(0.25)
        else:
            self.stop()
            raise RuntimeError(f"nested Hyprland never registered for {self.config}")
        # wait for the compositor to answer IPC
        env = self.env()
        for _ in range(80):
            p = subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True,
                               text=True, env=env)
            if p.returncode == 0 and p.stdout.strip().startswith("["):
                break
            time.sleep(0.25)
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    def stop(self):
        if self.proc is None:
            return
        try:
            self.dispatch("exit")
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception:
                pass
        self.proc = None

    # ---- IPC helpers ----------------------------------------------------
    def ctl(self, *args, js=True):
        cmd = ["hyprctl"] + (["-j"] if js else []) + list(args)
        p = subprocess.run(cmd, capture_output=True, text=True, env=self.env(),
                           timeout=60)
        if not js:
            return p.stdout
        try:
            return json.loads(p.stdout)
        except Exception:
            return {"_raw": p.stdout, "_err": p.stderr}

    def getoptions(self, names, chunk=60):
        """Batched `getoption` — descriptions.current is not live (issue #3)."""
        out = {}
        for i in range(0, len(names), chunk):
            part = names[i:i + chunk]
            batch = " ; ".join(f"getoption {n}" for n in part)
            p = subprocess.run(["hyprctl", "-j", "--batch", batch],
                               capture_output=True, text=True, env=self.env(),
                               timeout=120)
            txt = p.stdout.strip()
            try:
                vals = json.loads("[" + txt.replace("}\n\n{", "},{")
                                  .replace("}{", "},{") + "]") if txt else []
            except Exception:
                vals = []
            if len(vals) != len(part):
                for n in part:                       # fall back to one call each
                    out[n] = self.ctl("getoption", n)
                continue
            for n, v in zip(part, vals):
                out[n] = v
        return out

    def dump(self, option_names=None):
        state = {k: self.ctl(k) for k in DUMPS}
        if option_names:
            state["options"] = self.getoptions(option_names)
        return state

    def dispatch(self, legacy, args=""):
        """`hyprctl dispatch` is engine-dependent: a hyprlang session takes the
        legacy dispatcher name, a Lua session evaluates the argument as Lua. Reuse
        the importer's own translation table for the Lua form."""
        if self.config.endswith(".lua"):
            import dispatchers
            warns = []
            expr = dispatchers.translate(legacy, args, warns)
            if expr is None:
                return f"error: no lua form for {legacy}"
            return self.ctl("dispatch", expr, js=False)
        return self.ctl("dispatch", legacy, args, js=False) if args \
            else self.ctl("dispatch", legacy, js=False)

    def spawn(self, cmd):
        subprocess.run(["hyprctl", "dispatch", "exec", cmd], env=self.env(),
                       capture_output=True, timeout=30)

    def grim(self, out):
        p = subprocess.run(["grim", out], env=self.env(), capture_output=True,
                           text=True, timeout=60)
        return p.returncode == 0, p.stderr

    def grim_output(self, output, out):
        p = subprocess.run(["grim", "-o", output, out], env=self.env(),
                           capture_output=True, text=True, timeout=60)
        return p.returncode == 0, p.stderr
