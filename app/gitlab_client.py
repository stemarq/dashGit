"""Cliente assincrono da API REST v4 do GitLab.

Cobre paginacao por header, backoff em 429/5xx e limite de concorrencia
(gitlab.com corta em ~2000 req/min por token).
"""

import asyncio
import logging
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 4


class GitLabError(RuntimeError):
    pass


def encode_project(project: str | int) -> str:
    """`grupo/sub/projeto` -> `grupo%2Fsub%2Fprojeto`; IDs passam direto."""
    text = str(project)
    return text if text.isdigit() else quote(text, safe="")


class GitLabClient:
    def __init__(self, token: str | None = None, api_url: str | None = None):
        settings = get_settings()
        self.api_url = (api_url or settings.gitlab_api_url).rstrip("/")
        self.token = token or settings.gitlab_token
        if not self.token:
            raise GitLabError("GITLAB_TOKEN nao configurado (veja o .env.example)")
        self._client = httpx.AsyncClient(
            base_url=self.api_url,
            headers={"PRIVATE-TOKEN": self.token},
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self._sem = asyncio.Semaphore(settings.max_concurrency)

    async def __aenter__(self) -> "GitLabClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        delay = 1.0
        for attempt in range(MAX_RETRIES):
            async with self._sem:
                resp = await self._client.get(path, params=params)
            if resp.status_code in RETRY_STATUS and attempt < MAX_RETRIES - 1:
                wait = float(resp.headers.get("Retry-After", delay))
                log.warning("GitLab %s em %s, aguardando %.1fs", resp.status_code, path, wait)
                await asyncio.sleep(wait)
                delay *= 2
                continue
            if resp.status_code == 401:
                raise GitLabError("Token invalido ou sem escopo read_api")
            if resp.status_code == 404:
                raise GitLabError(f"Nao encontrado: {path}")
            resp.raise_for_status()
            return resp
        raise GitLabError(f"Falha apos {MAX_RETRIES} tentativas: {path}")

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return (await self._request(path, params)).json()

    async def paginate(
        self, path: str, params: dict[str, Any] | None = None, per_page: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        page_params = dict(params or {})
        page_params["per_page"] = per_page
        page: str | int = 1
        while page:
            page_params["page"] = page
            resp = await self._request(path, page_params)
            for item in resp.json():
                yield item
            page = resp.headers.get("X-Next-Page") or 0

    async def collect(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [item async for item in self.paginate(path, params)]

    # --- endpoints usados pelo dashboard -------------------------------

    async def project(self, project: str | int) -> dict[str, Any]:
        return await self.get(f"/projects/{encode_project(project)}")

    async def boards(self, project: str | int) -> list[dict[str, Any]]:
        return await self.collect(f"/projects/{encode_project(project)}/boards")

    async def board_lists(self, project: str | int, board_id: int) -> list[dict[str, Any]]:
        return await self.collect(
            f"/projects/{encode_project(project)}/boards/{board_id}/lists"
        )

    async def milestones(self, project: str | int) -> list[dict[str, Any]]:
        """Milestones do projeto e as herdadas do grupo (as sprints costumam
        viver no grupo, nao no projeto)."""
        return await self.collect(
            f"/projects/{encode_project(project)}/milestones",
            {"include_parent_milestones": "true"},
        )

    async def issues(
        self, project: str | int, updated_after: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"scope": "all", "order_by": "updated_at", "sort": "desc"}
        if updated_after:
            params["updated_after"] = updated_after
        return await self.collect(f"/projects/{encode_project(project)}/issues", params)

    async def commits(
        self, project: str | int, since: str | None = None
    ) -> list[dict[str, Any]]:
        """Commits de todos os branches, ja com as estatisticas de linhas.

        `with_stats` vem na propria listagem, entao nao ha um request por
        commit — diferente dos eventos de label.
        """
        params: dict[str, Any] = {"all": "true", "with_stats": "true"}
        if since:
            params["since"] = since
        return await self.collect(
            f"/projects/{encode_project(project)}/repository/commits", params
        )

    async def label_events(self, project: str | int, issue_iid: int) -> list[dict[str, Any]]:
        return await self.collect(
            f"/projects/{encode_project(project)}/issues/{issue_iid}/resource_label_events"
        )
