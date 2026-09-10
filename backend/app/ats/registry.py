from app.ats.base import ATSAdapter
from app.ats.generic import GenericATSAdapter
from app.ats.greenhouse import GreenhouseAdapter
from app.ats.personio import PersonioAdapter


class ATSRegistry:
    def __init__(self) -> None:
        self.adapters: list[ATSAdapter] = [
            GreenhouseAdapter(),
            PersonioAdapter(),
            GenericATSAdapter(),
        ]

    def for_url(self, url: str) -> ATSAdapter:
        return next(adapter for adapter in self.adapters if adapter.matches(url))
