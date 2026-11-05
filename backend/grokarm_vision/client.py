"""Client used by the robot server (`backend/session.py`) to ask the vision service.

The robot server keeps full ownership of motion: it turns `target_mm` into its
own proposal (hover height, IK, path checks) and still requires the user's
spoken confirmation before moving.
"""

from __future__ import annotations

import httpx


class VisionClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8710", timeout_s: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    async def locate(
        self,
        query: str,
        lang: str,
        expect_mm: tuple[float, float] | None = None,
        tolerance_mm: float = 15.0,
    ) -> dict:
        payload: dict = {"query": query, "lang": lang, "tolerance_mm": tolerance_mm}
        if expect_mm is not None:
            payload["expect_mm"] = list(expect_mm)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.post(f"{self.base_url}/api/locate", json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            return {"ok": False, "status": "service_unavailable", "message": f"vision service: {exc}"}

    async def status(self) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.get(f"{self.base_url}/api/status")
            response.raise_for_status()
            return response.json()
