"""Runpod Secure Cloud: REST lifecycle and GraphQL GPU catalogue.

No automatic POST retries: an ambiguous create response may already be billed.
"""
from __future__ import annotations

import math
import json
import re

import httpx

from ..config import get_settings
from ..credential_paths import provider_secret
from ..rental_cost import MIN_CLOUD_VRAM_GB, RUNPOD_GPU_NAMES

REST = "https://rest.runpod.io/v1"
GRAPHQL = "https://api.runpod.io/graphql"
IMAGE = "pytorch/pytorch:2.10.0-cuda13.0-cudnn9-runtime"
PLACEMENT = dict(minCudaVersion='13.0', minDownload=2000, minDisk=500,
                 minMemoryInGb=48, minVcpuCount=8, supportPublicIp=True)
BOOT = '''set -euo pipefail
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server
mkdir -p /root/.ssh /run/sshd /workspace
chmod 700 /root/.ssh
printf '%s\\n' "$PUBLIC_KEY" > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
ssh-keygen -A
exec /usr/sbin/sshd -D -e -o PermitRootLogin=prohibit-password -o PasswordAuthentication=no -o AllowTcpForwarding=yes -o ClientAliveInterval=15 -o ClientAliveCountMax=3 -o TCPKeepAlive=yes
'''


def configured() -> bool:
    return bool(provider_secret("runpod", get_settings().runpod_api_key_file))


def client() -> RunpodProvider:
    if not configured():
        raise RuntimeError("Save your Runpod API key in runpod.txt on the Tensor Master PC first")
    return RunpodProvider(provider_secret("runpod", get_settings().runpod_api_key_file))


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
        query = '''query($placement: GpuLowestPriceInput!) { gpuTypes { id displayName memoryInGb secureCloud
          lowestPrice(input: $placement) {
            stockStatus uninterruptablePrice availableGpuCounts
          } } }'''
        with httpx.Client(headers=self.headers, timeout=30, transport=self.transport) as api:
            response = api.post(GRAPHQL, json={"query": query, 'variables': {
                'placement': dict(gpuCount=1, secureCloud=True, **PLACEMENT)}})
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
        # v1 REST only exposes an exact CUDA set. GraphQL supports a floor while
        # retaining the same disk/network/CPU/RAM placement constraints.
        query = '''mutation($input: PodFindAndDeployOnDemandInput!) {
          podFindAndDeployOnDemand(input: $input) { id costPerHr }
        }'''
        payload = dict(name=name, imageName=IMAGE, cloudType='SECURE', gpuTypeId=gpu,
                       gpuCount=1, containerDiskInGb=150, volumeInGb=0, ports='22/tcp',
                       dockerArgs=json.dumps(dict(entrypoint=['/bin/bash', '-lc'], cmd=[BOOT])),
                       env=[dict(key='PUBLIC_KEY', value=public_key.strip())], **PLACEMENT)
        with httpx.Client(headers=self.headers, timeout=60, transport=self.transport) as api:
            response = api.post(GRAPHQL, json=dict(query=query, variables=dict(input=payload)))
        body = response.json()
        pod = (body.get('data') or {}).get('podFindAndDeployOnDemand')
        if pod and pod.get('id'):
            return pod
        detail = str(body.get('errors') or response.status_code)[:1500]
        for secret in (self.headers['Authorization'].removeprefix('Bearer '), public_key.strip()):
            if secret:
                detail = detail.replace(secret, '[REDACTED]')
        raise RuntimeError('Runpod deployment failed: ' + detail)
