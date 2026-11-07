"""Docker sandbox command construction, lifecycle, and integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydantic import ValidationError

from agentforge.sandbox.base import (
    SandboxConfig,
    SandboxError,
    SandboxStatus,
)
from agentforge.sandbox.docker import CommandOutput, DockerSandbox
from agentforge.sandbox.manager import SandboxManager

DOCKER_AVAILABLE, DOCKER_REASON = DockerSandbox.check_available()
requires_docker = pytest.mark.skipif(
    not DOCKER_AVAILABLE, reason=f"Docker unavailable: {DOCKER_REASON}"
)


class FakeRunner:
    def __init__(
        self,
        *,
        fail_run: bool = False,
        exec_stdout: str = "ok\n",
        exec_exit: int = 0,
        exec_timeout: bool = False,
    ) -> None:
        self.fail_run = fail_run
        self.exec_stdout = exec_stdout
        self.exec_exit = exec_exit
        self.exec_timeout = exec_timeout
        self.calls: list[list[str]] = []

    async def run(self, args, *, timeout, env=None, output_limit=65_536):
        self.calls.append(list(args))
        action = args[1] if len(args) > 1 else ""
        if action == "run":
            if self.fail_run:
                return CommandOutput("", "image missing", 1, False, 0.01)
            return CommandOutput("container_id", "", 0, False, 0.01)
        if action == "inspect":
            return CommandOutput("true\n", "", 0, False, 0.01)
        if action == "exec":
            return CommandOutput(
                self.exec_stdout,
                "" if self.exec_exit == 0 else "traceback",
                self.exec_exit,
                self.exec_timeout,
                0.01,
            )
        if action == "rm":
            return CommandOutput("", "", 0, False, 0.01)
        return CommandOutput("", "", 0, False, 0.01)


def config(tmp_path: Path) -> SandboxConfig:
    return SandboxConfig(
        workspace_root=tmp_path / "workspaces",
        cpu_limit=0.5,
        memory_limit_mb=128,
        pids_limit=32,
        timeout_seconds=10,
        output_limit_bytes=4096,
        tmpfs_size_mb=16,
    )


def test_docker_availability_report():
    available, detail = DockerSandbox.check_available()
    assert isinstance(available, bool)
    assert isinstance(detail, str)
    if not available:
        assert "docker" in detail.lower() or "daemon" in detail.lower()


def test_sandbox_config_rejects_root_user():
    with pytest.raises(ValidationError, match="must not be root"):
        SandboxConfig(container_user="0")
    with pytest.raises(ValidationError, match="must not be root"):
        SandboxConfig(container_user="root:root")


def test_run_args_are_locked_down(tmp_path):
    sandbox = DockerSandbox(
        "coding", config(tmp_path), runner=FakeRunner(), preflight=False
    )
    args = sandbox.build_run_args()
    joined = " ".join(args)
    assert "--network" in args
    assert args[args.index("--network") + 1] == "none"
    assert "--cap-drop" in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in args
    assert "no-new-privileges:true" in args
    assert "--read-only" in args
    assert "--pids-limit" in args
    assert "--memory" in args
    assert "--cpus" in args
    assert "--user" in args
    assert args[args.index("--user") + 1] == "65534:65534"
    assert "--mount" in args
    assert "target=/workspace" in args[args.index("--mount") + 1]
    assert "docker.sock" not in joined
    assert "privileged" not in joined
    assert "--env" in args
    assert not any(
        "API_KEY" in value or "TOKEN" in value for value in args if isinstance(value, str)
    )
    assert args[-2:] == ["sleep", "infinity"]


def test_execution_uses_container_not_host(tmp_path):
    runner = FakeRunner(exec_stdout="primes\n")
    sandbox = DockerSandbox(
        "coding",
        config(tmp_path),
        workspaces=None,
        runner=runner,
        preflight=False,
    )

    async def run():
        status = await sandbox.start()
        assert status is SandboxStatus.RUNNING
        result = await sandbox.execute_python(
            "print('primes')", filename="primes.py"
        )
        assert result.success
        assert result.stdout == "primes\n"
        exec_call = next(call for call in runner.calls if call[1] == "exec")
        assert "python" in exec_call
        assert "/workspace/scripts/primes.py" in exec_call
        assert await sandbox.health_check()
        await sandbox.stop()
        assert sandbox.status is SandboxStatus.STOPPED

    import asyncio

    asyncio.run(run())


def test_start_failure_reports_sandbox_error(tmp_path):
    sandbox = DockerSandbox(
        "coding",
        config(tmp_path),
        runner=FakeRunner(fail_run=True),
        preflight=False,
    )

    async def run():
        with pytest.raises(SandboxError, match="failed to start"):
            await sandbox.start()
        assert sandbox.status is SandboxStatus.FAILED

    import asyncio

    asyncio.run(run())


def test_exit_124_marks_timeout_and_stops(tmp_path):
    runner = FakeRunner(exec_stdout="", exec_exit=124, exec_timeout=False)
    sandbox = DockerSandbox(
        "coding", config(tmp_path), runner=runner, preflight=False
    )

    async def run():
        await sandbox.start()
        result = await sandbox.execute_python("while True: pass")
        assert result.timed_out is True
        assert result.success is False
        assert sandbox.status is SandboxStatus.STOPPED

    import asyncio

    asyncio.run(run())


def test_sandbox_manager_has_per_agent_workspaces(tmp_path):
    config_obj = config(tmp_path)
    manager = SandboxManager(config_obj)
    a = manager.get("coding")
    b = manager.get("research")
    assert a is not b
    assert manager.workspace_for("coding") != manager.workspace_for("research")
    assert manager.workspace_for("coding").is_relative_to(
        config_obj.workspace_root.resolve()
    )

    async def close():
        await manager.aclose()

    import asyncio

    asyncio.run(close())


@pytest.mark.docker
@requires_docker
def test_docker_integration_execution_and_isolation(tmp_path):
    config_obj = config(tmp_path)
    coding = DockerSandbox("coding", config_obj)
    research = DockerSandbox("research", config_obj)

    async def run():
        try:
            await coding.start()
            await research.start()
            uid = await coding.execute_python("import os; print(os.getuid())")
            assert uid.success, uid.stderr
            assert uid.stdout.strip() == "65534"

            await coding.write_text_file("secret.md", "coding only")
            read_ok = await coding.execute_python(
                "print(open('/workspace/secret.md', encoding='utf-8').read())"
            )
            assert read_ok.success
            assert "coding only" in read_ok.stdout

            with pytest.raises(SandboxError):
                await research.read_text_file("secret.md")

            isolated = await research.execute_python(
                "print(open('/workspace/secret.md', encoding='utf-8').read())"
            )
            assert not isolated.success
            assert isolated.exit_code != 0

            network = await coding.execute_python(
                "import socket\n"
                "try:\n"
                "    socket.create_connection(('1.1.1.1', 80), timeout=2)\n"
                "    print('network-reachable')\n"
                "except Exception as exc:\n"
                "    print('network-blocked', type(exc).__name__)\n"
            )
            assert "network-blocked" in network.stdout
        finally:
            await coding.stop()
            await research.stop()

    import asyncio

    asyncio.run(run())


@pytest.mark.docker
@requires_docker
def test_docker_integration_limits_are_configured(tmp_path):
    sandbox = DockerSandbox("coding", config(tmp_path))

    async def run():
        try:
            await sandbox.start()
            import asyncio
            import json

            process = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                sandbox.container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            data = json.loads(stdout)[0]
            host = data["HostConfig"]
            assert host["NetworkMode"] == "none"
            assert host["ReadonlyRootfs"] is True
            assert host["Memory"] == 128 * 1024 * 1024
            assert host["PidsLimit"] == 32
            assert host["CapDrop"] == ["ALL"]
        finally:
            await sandbox.stop()

    import asyncio

    asyncio.run(run())
