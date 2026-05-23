from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from backend.contracts.models import ToolDefinition, ToolDefinitionsResponse, ToolRunResponse


SESSION_HEADER = "x-lunar-session-token"


@dataclass(frozen=True)
class NotebookClientConfig:
    base_url: str
    api_token: str
    timeout_seconds: float = 30.0


class NotebookClient:
    """Thin helper for notebook-driven REST + WS interactions via FastAPI."""

    def __init__(self, config: NotebookClientConfig, client: httpx.Client | None = None) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            headers={SESSION_HEADER: config.api_token},
        )

    @classmethod
    def open_session(
        cls,
        *,
        base_url: str,
        client_name: str,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> "NotebookClient":
        own_client = client or httpx.Client(base_url=base_url, timeout=timeout_seconds)
        response = own_client.post("/api/v1/notebook/sessions", json={"client_name": client_name})
        response.raise_for_status()
        payload = response.json()
        token = payload["api_token"]
        cfg = NotebookClientConfig(base_url=base_url, api_token=token, timeout_seconds=timeout_seconds)
        if client is None:
            own_client.close()
        return cls(config=cfg, client=client)

    @property
    def session_token(self) -> str:
        return self._config.api_token

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def create_scenario(self, *, scenario_root: str, name: str, owner: str) -> dict[str, Any]:
        response = self._client.post(
            "/api/v1/scenarios",
            json={"scenario_root": scenario_root, "name": name, "owner": owner},
        )
        response.raise_for_status()
        return response.json()

    def register_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post("/api/v1/products", json=payload)
        response.raise_for_status()
        return response.json()

    def create_layer(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post("/api/v1/layers", json=payload)
        response.raise_for_status()
        return response.json()

    def import_geotiff(
        self,
        *,
        scenario_id: str,
        source_path: str,
        kind: str = "analysis",
        subkind: str = "notebook_output",
        bypass_cog: bool = True,
    ) -> dict[str, Any]:
        response = self._client.post(
            f"/api/v1/scenarios/{scenario_id}/imports/geotiff",
            json={
                "source_path": source_path,
                "kind": kind,
                "subkind": subkind,
                "bypass_cog": bypass_cog,
            },
        )
        response.raise_for_status()
        return response.json()

    def list_product_files(self, product_id: str) -> list[dict[str, Any]]:
        response = self._client.get(f"/api/v1/products/{product_id}/files")
        response.raise_for_status()
        return response.json()

    def zoom_map_to_file(
        self,
        *,
        scenario_id: str,
        file_id: str,
        padding_px: int | None = None,
        max_zoom: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"file_id": file_id}
        if padding_px is not None:
            payload["padding_px"] = int(padding_px)
        if max_zoom is not None:
            payload["max_zoom"] = float(max_zoom)
        response = self._client.post(
            f"/api/v1/scenarios/{scenario_id}/map-commands/zoom-to-file",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    def import_geotiff_create_layer_and_zoom(
        self,
        *,
        scenario_id: str,
        source_path: str,
        title: str,
        kind: str = "analysis",
        subkind: str = "notebook_output",
        bypass_cog: bool = True,
        z_index: int = 50,
        style: dict[str, Any] | None = None,
        padding_px: int | None = None,
        max_zoom: float | None = None,
    ) -> dict[str, Any]:
        product = self.import_geotiff(
            scenario_id=scenario_id,
            source_path=source_path,
            kind=kind,
            subkind=subkind,
            bypass_cog=bypass_cog,
        )
        files = self.list_product_files(product["product_id"])
        if not files:
            raise RuntimeError(f"No files registered for product {product['product_id']}.")
        source_file_id = files[-1]["file_id"]
        layer = self.create_layer(
            {
                "scenario_id": scenario_id,
                "product_id": product["product_id"],
                "title": title,
                "visible": True,
                "opacity": 1.0,
                "z_index": int(z_index),
                "render_mode": "raster",
                "source_file_id": source_file_id,
                "style": style or {"colormap": "gray", "brightness": 0.0, "contrast": 1.0},
            }
        )
        zoom = self.zoom_map_to_file(
            scenario_id=scenario_id,
            file_id=source_file_id,
            padding_px=padding_px,
            max_zoom=max_zoom,
        )
        return {
            "scenario_id": scenario_id,
            "product": product,
            "file_id": source_file_id,
            "layer": layer,
            "map_zoom": zoom,
        }

    def list_layers(self, scenario_id: str) -> list[dict[str, Any]]:
        response = self._client.get(f"/api/v1/scenarios/{scenario_id}/layers")
        response.raise_for_status()
        return response.json()

    def list_tools(self, *, include_drafts: bool = False, include_system: bool = True) -> ToolDefinitionsResponse:
        response = self._client.get(
            "/api/v1/tools",
            params={"include_drafts": include_drafts, "include_system": include_system},
        )
        response.raise_for_status()
        return ToolDefinitionsResponse.model_validate(response.json())

    def get_tool(self, tool_name: str, *, include_drafts: bool = False, include_system: bool = True) -> ToolDefinition:
        response = self._client.get(
            f"/api/v1/tools/{tool_name}",
            params={"include_drafts": include_drafts, "include_system": include_system},
        )
        response.raise_for_status()
        return ToolDefinition.model_validate(response.json())

    def run_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolRunResponse:
        response = self._client.post(f"/api/v1/tools/{tool_name}/runs", json={"arguments": arguments})
        response.raise_for_status()
        return ToolRunResponse.model_validate(response.json())

    async def ws_events(self) -> AsyncIterator[dict[str, Any]]:
        try:
            import websockets
        except Exception as exc:
            raise RuntimeError("websockets package is required for NotebookClient.ws_events().") from exc

        ws_url = _to_ws_url(self._config.base_url, "/api/v1/notebook/events", self._config.api_token)
        async with websockets.connect(ws_url) as websocket:
            while True:
                message = await websocket.recv()
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                yield json.loads(message)


def _to_ws_url(base_url: str, path: str, token: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    query = urlencode({"token": token})
    return urlunparse((scheme, parsed.netloc, path, "", query, ""))
