#!/usr/bin/env python3
"""Run the clean finite-window EWMA OAT search at 5k CTX x 5 rounds."""
import subprocess
import sys
import json
import socket
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = HERE.parent / "exp010_tdr_parameter_search" / "run.py"
sys.path.insert(0, str(ROOT))
from brokerlab.config import load_config  # noqa: E402

NUM_SHARDS = 16
STATIC_PORT_END = 49152


def _argument_value(args, option):
    for index, value in enumerate(args):
        if value == option and index + 1 < len(args):
            return args[index + 1]
        if value.startswith(option + "="):
            return value.split("=", 1)[1]
    return None


def _explicit_base_port(args):
    for index, value in enumerate(args):
        setting = None
        if value == "--set" and index + 1 < len(args):
            setting = args[index + 1]
        elif value.startswith("--set="):
            setting = value.split("=", 1)[1]
        if setting and setting.startswith("chain.base_port="):
            return int(setting.split("=", 1)[1])
    return None


def _port_block_available(base_port):
    sockets = []
    try:
        for port in range(base_port, base_port + NUM_SHARDS):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
            sockets.append(probe)
        return True
    except OSError:
        return False
    finally:
        for probe in sockets:
            probe.close()


def _find_port_block(preferred):
    candidates = list(range(max(1024, preferred), STATIC_PORT_END - NUM_SHARDS))
    candidates.extend(range(1024, min(preferred, STATIC_PORT_END - NUM_SHARDS)))
    for base_port in candidates:
        if _port_block_available(base_port):
            return base_port
    raise RuntimeError("No free contiguous block of 16 localhost ports was found.")


def _resume_manifest(args):
    resume = _argument_value(args, "--resume")
    if not resume:
        return None, None
    path = Path(resume).resolve() / "manifest.json"
    if not path.is_file():
        raise SystemExit(f"Resume manifest not found: {path}")
    return path, json.loads(path.read_text(encoding="utf-8"))


def main():
    args = list(sys.argv[1:])
    preferred = int(load_config(HERE / "config.yaml").chain.base_port)
    manifest_path, manifest = _resume_manifest(args)
    explicit_port = _explicit_base_port(args)
    saved_overrides = dict((manifest or {}).get("cli_overrides") or {})
    saved_port = int(saved_overrides["chain.base_port"]) \
        if saved_overrides.get("chain.base_port") is not None else None

    if explicit_port is not None:
        base_port = explicit_port
        if not _port_block_available(base_port):
            raise SystemExit(
                f"Requested chain.base_port={base_port} is occupied within its "
                "16-port block. Choose another free block or omit the override.")
    elif saved_port is not None and _port_block_available(saved_port):
        base_port = saved_port
    else:
        base_port = _find_port_block(saved_port or preferred)

    if manifest is not None and saved_port != base_port:
        saved_overrides["chain.base_port"] = base_port
        manifest["cli_overrides"] = saved_overrides
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if explicit_port is None:
        args.extend(["--set", f"chain.base_port={base_port}"])
    print(f"exp022 search Anvil ports: {base_port}-{base_port + NUM_SHARDS - 1}", flush=True)
    cmd = [
        sys.executable, str(BASE),
        "--config", str(HERE / "config.yaml"),
        "--space", str(HERE / "search_space.yaml"),
        "--profile", "search",
        "--stage", "oat",
        "--only", "epsilon,ewma_half_life,ewma_q_min,ewma_chi_blocks",
        "--output-root", str(HERE / "out"),
        *args,
    ]
    started_at = time.time()
    result = subprocess.call(cmd, cwd=ROOT)
    if result != 0 or "--dry-run" in args or "--no-plot" in args:
        return result

    resume = _argument_value(args, "--resume")
    if resume:
        run_dir = Path(resume).resolve()
    else:
        manifests = [path for path in (HERE / "out").glob("*/manifest.json")
                     if path.stat().st_mtime >= started_at - 1]
        if not manifests:
            print("Could not locate the newly completed run; paper sensitivity "
                  "boxplots were not generated.", file=sys.stderr)
            return 1
        run_dir = max(manifests, key=lambda path: path.stat().st_mtime).parent

    print("Generating the four-parameter paper sensitivity boxplots...", flush=True)
    return subprocess.call([
        sys.executable, str(HERE / "plot_sensitivity.py"),
        "--run", str(run_dir),
    ], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
