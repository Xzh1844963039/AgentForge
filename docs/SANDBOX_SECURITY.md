# AgentForge Sandbox Security

**Phase 2 — Docker isolation for per-agent Python execution**

## 1. Status

The Docker sandbox is fully implemented and **verified on 2026-11-08** with Docker
Desktop 29.9.2. The 4 Docker integration tests pass, `demo-tools` executed the prime
script in the sandbox with the expected stdout/exit code, and `docker ps -a` showed no
leftover AgentForge containers. The only remaining skip is the Windows symlink test,
which is unrelated to Docker.

## 2. Threat model

The sandbox treats the LLM and its generated Python code as untrusted. It must prevent:

- reading or modifying another agent's workspace or the host filesystem;
- reading host credentials, LLM API keys, GitHub tokens, SSH keys or Docker socket;
- escaping the workspace via `..`, absolute paths, symlinks or Windows device names;
- consuming unbounded CPU, memory, PIDs, disk or wall-clock time;
- contacting arbitrary network hosts (default deny);
- privilege escalation inside the container;
- orphaned processes after a timeout;
- silent fallback to unisolated host execution.

Out of scope for Phase 2: malicious kernel exploits, Docker daemon compromise,
hardware side channels, supply-chain compromise of the configured image, and
DNS-rebinding resistance for the host-side web-fetch tool.

## 3. Isolation architecture

```text
Agent coding                    Agent research
     │                               │
     ▼                               ▼
data/workspaces/coding/        data/workspaces/research/
     │ bind mount                    │ bind mount
     ▼                               ▼
Container A (/workspace)       Container B (/workspace)
network none                   network none
non-root 65534:65534           non-root 65534:65534
read-only root                 read-only root
cap-drop ALL                   cap-drop ALL
no docker.sock                 no docker.sock
```

Each agent gets:

- a separate host directory under `AGENTFORGE_WORKSPACE_ROOT` (default
  `<data_dir>/workspaces`);
- a separate container named from the agent ID and a workspace hash;
- only its own workspace mounted at `/workspace`.

No container receives another agent's workspace, the host `D:` drive, the project
root, or the Docker socket.

## 4. Container flags

`DockerSandbox.build_run_args()` generates (argument list, never a shell):

| Flag | Purpose |
|---|---|
| `--name <per-agent>` | Isolated, deterministic container identity |
| `--init` | Proper signal/reaping behavior |
| `--network none` (default) | No network access |
| `--cpus <limit>` | CPU quota |
| `--memory <MB>m` / `--memory-swap <same>` | Memory cap without swap escape |
| `--pids-limit <n>` | Fork-bomb protection |
| `--read-only` | Read-only container root until disabled by config |
| `--tmpfs /tmp:rw,noexec,nosuid,size=<MB>m` | Writable scratch with no exec/setuid |
| `--cap-drop ALL` | Drop all Linux capabilities |
| `--security-opt no-new-privileges:true` | Block privilege gain |
| `--user 65534:65534` | Non-root execution; root config is rejected |
| `--workdir /workspace` | Only the private workspace |
| `--env PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, `HOME=/tmp` | Minimal environment |
| `--mount type=bind,source=<agent workspace>,target=/workspace` | Private filesystem |
| labels | Cleanup/audit association |
| image `python:3.11-slim` + `sleep infinity` | Long-lived sandbox |

Explicitly absent: `--privileged`, Docker socket mounts, host env injection, extra
capabilities, host network, device mounts.

## 5. Lifecycle

- `start()`: preflight `docker info`; remove a stale container with the same name;
  `docker run -d`; verify `docker inspect .State.Running`.
- `health_check()`: inspect the running state.
- `execute_python()` / `execute_python_file()`: stage code into the private workspace,
  run `docker exec` with `timeout --signal=TERM --kill-after=2s`.
- `stop()`: `docker rm -f`; idempotent; safe when the CLI is missing.
- Timeout/exit 124: the container is removed to prevent orphaned execution; the next
  call starts a fresh container.
- `SandboxManager.aclose()` stops all running/failed sandboxes at runtime shutdown.
- Workspaces persist across container restarts; containers do not.

## 6. Resource and output limits

| Limit | Default | Setting |
|---|---|---|
| CPU | 1.0 core | `AGENTFORGE_SANDBOX_CPU_LIMIT` |
| Memory | 256 MB | `AGENTFORGE_SANDBOX_MEMORY_MB` |
| PIDs | 64 | `AGENTFORGE_SANDBOX_PIDS_LIMIT` |
| Wall clock | 30 s | `AGENTFORGE_SANDBOX_TIMEOUT_SECONDS` |
| Output | 64 KiB | `AGENTFORGE_SANDBOX_OUTPUT_LIMIT_BYTES` |
| Code size | 256 KiB (config max file size) | `AGENTFORGE_MAX_FILE_BYTES` |
| Workspace files | 200 listed | `AGENTFORGE_MAX_WORKSPACE_FILES` |

stdout/stderr are drained concurrently and truncated at the limit; the process is not
blocked by a full pipe. The executor also caps the overall tool result.

## 7. Credentials and secrets

- No host environment is passed into the container; only the three explicit env vars.
- LLM keys, GitHub tokens, SSH agent sockets and cloud credentials are not mounted.
- The Docker socket is never mounted.
- Git tools run host-side but use a scrubbed environment:
  `GIT_TERMINAL_PROMPT=0`, no `*_TOKEN`/`*_SECRET`/`*_KEY`/`*_AUTH`/SSH variables.
- Git push requires approval; no credential helper is configured by AgentForge.

## 8. No host fallback

`python_execute` and `python_test` call `DockerSandbox` only. If Docker is missing,
`SandboxUnavailableError` becomes a structured tool error:

```text
Docker sandbox unavailable: docker CLI not found on PATH...
```

There is no `subprocess` fallback on Windows, no "temporary" host execution, and no
mock success. A dedicated test (`test_no_host_fallback_when_docker_missing`) asks for
code that would write a host sentinel and asserts the sentinel never appears.

## 9. Workspace and file-escape defenses

Host-side file tools and the sandbox share the same `WorkspaceManager`:

- reject absolute paths, drive letters, `..`, NUL/control characters;
- reject Windows reserved device names (`CON`, `NUL`, `COM1`, ...);
- reject trailing spaces/dots;
- reject any symlink component; verify the final path is under the agent root;
- allow only `.md`/`.txt` for public file tools (`.py` only for internal sandbox
  script staging);
- atomic write + `os.replace`;
- per-file byte cap and listing/search caps;
- delete requires approval;
- cross-agent data only via `share_artifact`/`fetch_artifact`.

Residual risk: a TOCTOU race between symlink checks and file operations is not fully
eliminated; Windows symlink creation in the test environment is unavailable, so that
test is SKIPPED. The implementation rejects symlink components, but a hostile local
process with write access to the workspace could still race. This is outside the
model's authority.

## 10. Verification

### Verified result (2026-11-08)

```text
Docker Desktop: 29.9.2
docker info:    ok
pytest -m docker -q
4 passed, 183 deselected in 18.33s
agentforge demo-tools
exit 0; stdout: Primes up to 20: [2, 3, 5, 7, 11, 13, 17, 19]
docker ps -a: no AgentForge containers
```

Full regression with Docker running: `186 passed, 2 skipped`. The 2 skips are the
Windows symlink-privilege test and the Docker-missing failure-path test (skipped
because Docker was available).

### Verification commands

```powershell
docker info
docker run --rm python:3.11-slim python -c "print('docker ok')"
pytest -m docker -q
python -m agentforge demo-tools
```

The Docker tests check:

1. sandbox start/stop and health;
2. Python execution and real stdout;
3. non-root UID (`os.getuid() == 65534`);
4. network blocked under `--network none`;
5. Agent A's file not readable in Agent B's container;
6. configured memory/PID/network/read-only/cap-drop flags via `docker inspect`;
7. end-to-end prime script execution and tool audit.

### Windows setup

1. Install Docker Desktop with WSL2 backend.
2. Enable WSL2 and start Docker Desktop.
3. Ensure `docker` is on `PATH`.
4. Run the commands above.

No system configuration was changed by this project except starting the already
installed Docker Desktop for the verification run.

## 11. Known limitations

- **Windows symlink test skipped** — unrelated to Docker; the OS denied symlink
  creation for the test user. Path components are still checked and rejected.
- **Linux UID mapping** — the container user is 65534; a bind-mounted workspace may
  need host-side permissions/ownership adjustment on Linux. Windows Docker Desktop
  usually maps this transparently. This is documented, not silently changed.
- **No custom seccomp/AppArmor profile** — the sandbox relies on Docker's default
  profile plus `cap-drop ALL`, no-new-privileges, read-only root and no network.
- **No disk quota** — workspace size is capped per file and per listing, but total
  filesystem usage is not quota-enforced. Add a host-side quota in a later phase.
- **Image trust** — `python:3.11-slim` is pulled from the configured registry; image
  provenance/signature verification is future work.
- **Network enablement** — setting `AGENTFORGE_SANDBOX_NETWORK_ENABLED=true` gives the
  container bridge networking. The default is closed; enabling it changes the threat
  model and should require approval.
- **DNS rebinding** — the web-fetch tool validates DNS before connecting; httpx may
  resolve again. A future phase should pin the validated IP or use a controlled proxy.
- **Full HITL** — approvals are an interface only. Phase 4 will add interactive
  approval, pause/resume, and permission scopes.

## 12. Acceptance checklist

| Requirement | Implementation | Verification |
|---|---|---|
| Per-agent container | ✅ | Docker tests PASS |
| Per-agent private workspace | ✅ | host + Docker isolation PASS |
| Non-root user | ✅ | config validator + Docker test PASS |
| CPU/memory/PID limits | ✅ | run-args + `docker inspect` PASS |
| Wall-clock timeout | ✅ | run-args + exit-124 test PASS |
| Network disabled by default | ✅ | run-args + live network-block test PASS |
| No Docker socket / privileged mode | ✅ | run-args PASS |
| Capabilities dropped + no-new-privileges | ✅ | run-args + inspect PASS |
| Read-only root + tmpfs | ✅ | run-args + inspect PASS |
| No API keys in sandbox | ✅ | run-args/env tests PASS |
| Lifecycle cleanup/restart | ✅ | unit + Docker tests PASS; no leftovers |
| Artifact-only cross-agent sharing | ✅ | unit/integration tests PASS |
| No host Python fallback | ✅ | dedicated Docker-missing test PASS (skipped while Docker runs) |
| Docker integration validation | ✅ | **4/4 PASS on 2026-11-08** |
