from dataclasses import dataclass


class RentalDenied(ValueError):
    """Raised when a requested rental would violate a spending guardrail."""


@dataclass(frozen=True)
class RentalPolicy:
    max_active_gpus: int
    max_gpu_price_per_hour: float
    max_total_price_per_hour: float

    def validate(
        self,
        *,
        requested_gpu_count: int,
        offer_price_per_hour: float,
        active_gpu_count: int,
        active_price_per_hour: float,
    ) -> None:
        if requested_gpu_count < 1:
            raise RentalDenied("At least one GPU must be requested.")
        if offer_price_per_hour <= 0:
            raise RentalDenied("The offer price must be positive.")
        if offer_price_per_hour > self.max_gpu_price_per_hour:
            raise RentalDenied("The offer exceeds the per-GPU price limit.")
        if active_gpu_count + requested_gpu_count > self.max_active_gpus:
            raise RentalDenied("The request exceeds the active GPU limit.")
        requested_total = offer_price_per_hour * requested_gpu_count
        if active_price_per_hour + requested_total > self.max_total_price_per_hour:
            raise RentalDenied("The request exceeds the fleet hourly price limit.")
