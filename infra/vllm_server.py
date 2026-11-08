"""Serve one open-weight model on a Modal GPU through vLLM's OpenAI-compatible API.

Deploy:   uv run --group modal modal deploy infra/vllm_server.py
Stop:     uv run --group modal modal app stop swarm-vllm
Other model: SWARM_APP=swarm-vllm-32b SWARM_MODEL=Qwen/Qwen3-32B-FP8 SWARM_MODEL_REVISION=<sha> \
          SWARM_GPU=H100 uv run --group modal modal deploy infra/vllm_server.py

Requests need ``Authorization: Bearer $VLLM_API_KEY``; the key lives in the Modal Secret
``swarm-vllm`` (see docs/running.md). Adapted from https://modal.com/docs/examples/vllm_inference.
"""

import os
import subprocess

import modal

MODEL_NAME = os.environ.get("SWARM_MODEL", "Qwen/Qwen3-8B")
MODEL_REVISION = os.environ.get("SWARM_MODEL_REVISION", "b968826d9c46dd6066d109eabc6255188de91218")
GPU = os.environ.get("SWARM_GPU", "L40S")
MAX_MODEL_LEN = 16384
VLLM_PORT = 8000
MINUTES = 60

image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install("vllm==0.21.0")
    .env(
        {
            "HF_XET_HIGH_PERFORMANCE": "1",
            "SWARM_APP": os.environ.get("SWARM_APP", "swarm-vllm"),
            "SWARM_MODEL": MODEL_NAME,
            "SWARM_MODEL_REVISION": MODEL_REVISION,
        }
    )
)

hf_cache = modal.Volume.from_name("huggingface-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("vllm-cache", create_if_missing=True)

app = modal.App(os.environ.get("SWARM_APP", "swarm-vllm"))


@app.function(
    image=image,
    gpu=GPU,
    scaledown_window=5 * MINUTES,
    timeout=60 * MINUTES,
    max_containers=1,
    volumes={"/root/.cache/huggingface": hf_cache, "/root/.cache/vllm": vllm_cache},
    secrets=[modal.Secret.from_name("swarm-vllm")],
)
@modal.concurrent(max_inputs=128)
@modal.web_server(port=VLLM_PORT, startup_timeout=20 * MINUTES)
def serve():
    cmd = [
        "vllm",
        "serve",
        MODEL_NAME,
        "--revision",
        MODEL_REVISION,
        "--served-model-name",
        "llm",
        "--host",
        "0.0.0.0",
        "--port",
        str(VLLM_PORT),
        "--api-key",
        os.environ["VLLM_API_KEY"],
        "--max-model-len",
        str(MAX_MODEL_LEN),
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "hermes",
        "--enable-prefix-caching",
    ]
    subprocess.Popen(cmd)
