from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OfferSearch:
    gpu_names: tuple[str, ...]
    min_vram_gb: int
    max_price_per_hour: float
    min_reliability: float
    disk_gb: int
    extra_query: str = ''          # e.g. 'id=12345' to fetch one picked listing (the default result page is small)


@dataclass(frozen=True)
class GPUOffer:
    id: int
    gpu_name: str
    vram_gb: float
    price_per_hour: float
    reliability: float
    raw: dict[str, Any]


class GPUProvider(ABC):
    @abstractmethod
    def search_offers(self, search: OfferSearch) -> list[GPUOffer]: ...

    @abstractmethod
    def create_instance(self, offer_id: int, *, image: str, disk_gb: int) -> int: ...

    @abstractmethod
    def list_instances(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def destroy_instance(self, instance_id: int) -> None: ...
