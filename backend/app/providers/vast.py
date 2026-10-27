from typing import Any

from vastai import VastAI

from .base import GPUOffer, GPUProvider, OfferSearch


class VastProvider(GPUProvider):
    """All Vast-specific behavior stays behind this adapter."""

    def __init__(self, api_key: str) -> None:
        self._client = VastAI(api_key=api_key, raw=True)

    def search_offers(self, search: OfferSearch) -> list[GPUOffer]:
        rows: list[dict[str, Any]] = []
        for gpu_name in search.gpu_names:
            query = (
                f"rentable=true verified=true num_gpus=1 gpu_name={gpu_name} "
                f"gpu_ram>={search.min_vram_gb} "
                f"dph_total<={search.max_price_per_hour} "
                f"reliability>={search.min_reliability} "
                f"disk_space>={search.disk_gb}"
                + (f" {search.extra_query}" if search.extra_query else "")
            )
            # limit: the default page is small, so a specific host the operator picked could be missing from it
            rows.extend(self._client.search_offers(query=query, storage=search.disk_gb, limit=1000, order='dph_total'))
        return [
            GPUOffer(
                id=int(row["id"]),
                gpu_name=str(row["gpu_name"]),
                vram_gb=float(row["gpu_ram"]) / 1024,
                price_per_hour=float(row["dph_total"]),
                reliability=float(row["reliability"]),
                raw=row,
            )
            for row in sorted(rows, key=lambda item: float(item["dph_total"]))
        ]

    def create_instance(self, offer_id: int, *, image: str, disk_gb: int) -> int:
        result = self._client.create_instance(id=offer_id, image=image, disk=disk_gb)
        return int(result["new_contract"])

    def list_instances(self) -> list[dict[str, Any]]:
        return self._client.show_instances()

    def destroy_instance(self, instance_id: int) -> None:
        self._client.destroy_instance(id=instance_id)
