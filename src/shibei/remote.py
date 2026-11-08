"""远程 brain — 把另一个拾贝 daemon（或兼容 /v1/recall 的服务）当成一个库。

注册表用法：

.. code-block:: yaml

    noteai:
      url: http://127.0.0.1:47821
      token: "..."        # 可选
      brain: default      # 可选：远端多库 daemon 里要查的库名
      timeout: 30
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class RemoteBrainError(RuntimeError):
    pass


class RemoteBrain:
    """最小远程代理：只实现 recall / list_chunks，够联邦与 TUI 用。"""

    def __init__(
        self,
        name: str,
        url: str,
        token: str = "",
        brain: str = "",
        timeout: float = 30.0,
    ):
        self.name = name
        self.url = url.rstrip("/")
        self.token = token
        self.remote_brain = brain
        self.timeout = timeout

    # ---------- HTTP ----------

    def _request(self, method: str, path: str, body: 'dict | None' = None) -> Any:
        payload = None
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = Request(self.url + path, data=payload, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace") if exc.fp else str(exc)
            raise RemoteBrainError(f"{self.name}: HTTP {exc.code} {detail}") from exc
        except URLError as exc:
            raise RemoteBrainError(f"{self.name}: {exc.reason}") from exc

    # ---------- 与本地 Shibei 对齐的接口 ----------

    def recall(self, **kwargs: Any) -> dict:
        body = dict(kwargs)
        if self.remote_brain:
            body["brain"] = self.remote_brain
        else:
            body.pop("brain", None)
        result = self._request("POST", "/v1/recall", body)
        if not isinstance(result, dict):
            raise RemoteBrainError(f"{self.name}: 非法响应")
        for item in result.get("results") or []:
            # 对聚合方来说，这批结果统一来自 registry 里的这个名字
            item["brain"] = self.name
        result["brain"] = self.name
        return result

    def list_chunks(
        self,
        limit: int = 200,
        offset: int = 0,
        source: 'str | None' = None,
        tag: 'str | None' = None,
    ) -> dict:
        query: dict[str, Any] = {"limit": limit, "offset": offset}
        if source:
            query["source"] = source
        if tag:
            query["tag"] = tag
        if self.remote_brain:
            query["brain"] = self.remote_brain
        return self._request("GET", "/v1/chunks?" + urlencode(query))

    def health(self) -> dict:
        return self._request("GET", "/v1/health")

    def describe(self) -> dict:
        info = {
            "name": self.name,
            "remote": True,
            "url": self.url,
            "brain": self.remote_brain,
        }
        try:
            data = self.health()
            info["version"] = data.get("version")
            info["brains"] = data.get("brains")
        except RemoteBrainError as exc:
            info["error"] = str(exc)
        return info
