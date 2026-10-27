"""Runpod Secure Cloud: REST lifecycle and GraphQL GPU catalogue.

No automatic POST retries: an ambiguous create response may already be billed.
"""
from __future__ import annotations

import math
import re

import httpx

from ..config import get_settings
from ..rental_cost import MIN_CLOUD_VRAM_GB, RUNPOD_GPU_NAMES

REST = "https://rest.runpod.io/v1"
GRAPHQL = "https://api.runpod.io/graphql"
IMAGE = "pytorch/pytorch:2.10.0-cuda13.0-cudnn9-runtime"
BOOT = '''set -euo pipefail
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server
mkdir -p /root/.ssh /run/sshd /workspace
chmod 700 /root/.ssh
printf '%s\\n' "$PUBLIC_KEY" > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
ssh-keygen -A
exec /usr/sbin/sshd -D -e -o PermitRootLogin=prohibit-password -o PasswordAuthentication=no -o AllowTcpForwarding=yes
'''


def configured() -> bool:
    path = get_settings().runpod_api_key_file
    return path.is_file() and bool(path.read_text(encoding="utf-8").strip())


def client() -> RunpodProvider:
    if not configured():
        raise RuntimeError("Save your Runpod API key in runpod.txt on the Tensor Master PC first")
    return RunpodProvider(get_settings().runpod_api_key_file.read_text(encoding="utf-8").strip())


class RunpodProvider:
    def __init__(self, key: str, *, transport=None):
        self.headers = {"Authorization": f"Bearer {key}"}
        self.transport = transport

    def request(self, method: str, path: str, **kwargs):
        with httpx.Client(headers=self.headers, timeout=60, transport=self.transport) as api:
            response = api.request(method, REST + path, **kwargs)
        if response.status_code == 404 and method == "GET":
            return None
        if response.is_error:
            # Keep useful provider diagnostics without echoing request secrets.
            detail = response.text[:1500]
            for secret in (self.headers["Authorization"].removeprefix("Bearer "),
                           (kwargs.get("json") or {}).get("env", {}).get("PUBLIC_KEY", "")):
                if secret:
                    detail = detail.replace(secret, "[REDACTED]")
            raise RuntimeError(f"Runpod {method} {path} returned HTTP {response.status_code}: {detail}")
        return response.json() if response.content else None

    def offers(self, maximum: float) -> list[dict]:
        query = '''query { gpuTypes { id displayName memoryInGb secureCloud
          lowestPrice(input: {gpuCount: 1, secureCloud: true}) {
            stockStatus uninterruptablePrice availableGpuCounts
          } } }'''
        with httpx.Client(headers=self.headers, timeout=30, transport=self.transport) as api:
            response = api.post(GRAPHQL, json={"query": query})
        if response.is_error:
            raise RuntimeError(f"Runpod GPU catalogue returned HTTP {response.status_code}")
        body = response.json()
        if body.get("errors"):
            raise RuntimeError("Runpod rejected the GPU catalogue query; check API key permissions")
        offers = []
        for row in body["data"]["gpuTypes"]:
            if not any(name in row["id"] for name in RUNPOD_GPU_NAMES) or "MIG" in row["id"]:
                continue
            quote = row.get("lowestPrice") or {}
            price = float(quote.get("uninterruptablePrice") or 0)
            if (not row.get("secureCloud") or quote.get("stockStatus") in (None, "None")
                    or (quote.get("availableGpuCounts") is not None and 1 not in quote["availableGpuCounts"])
                    or float(row.get("memoryInGb") or 0) < MIN_CLOUD_VRAM_GB
                    or not math.isfinite(price) or price <= 0 or price + 0.05 > maximum):
                continue
            offers.append({"id": row["id"], "gpu": row["displayName"],
                           "vram_gb": row["memoryInGb"], "price_per_hour": price + 0.05,
                           "gpu_price_per_hour": price, "stock": quote["stockStatus"],
                           "datacenter": True, "provider": "runpod",
                           "note": "Secure Cloud · GPU quote + $0.05/hr storage allowance; placement confirmed at launch"})
        return sorted(offers, key=lambda item: item["price_per_hour"])

    def list_instances(self) -> list[dict]:
        return self.request("GET", "/pods") or []

    @staticmethod
    def pod_path(pod_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", pod_id):
            raise ValueError("Invalid Runpod Pod ID")
        return "/pods/" + pod_id

    def get_instance(self, pod_id: str):
        return self.request("GET", self.pod_path(pod_id))

    def destroy_instance(self, pod_id: str):
        if self.get_instance(pod_id) is not None:
            self.request("DELETE", self.pod_path(pod_id))

    def create_instance(self, gpu: str, *, name: str, public_key: str):
        return self.request("POST", "/pods", json={
            "name": name, "imageName": IMAGE, "cloudType": "SECURE",
            "computeType": "GPU", "gpuTypeIds": [gpu], "gpuCount": 1,
            "interruptible": False, "allowedCudaVersions": ["13.0"],
            "containerDiskInGb": 150, "volumeInGb": 0,
            "minDownloadMbps": 2000, "minDiskBandwidthMBps": 500,
            "minRAMPerGPU": 48, "minVCPUPerGPU": 8,
            "supportPublicIp": True, "ports": ["22/tcp"],
            "dockerEntrypoint": ["/bin/bash", "-lc"], "dockerStartCmd": [BOOT],
            "env": {"PUBLIC_KEY": public_key.strip()},
        })
