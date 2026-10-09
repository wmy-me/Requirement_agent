#!/usr/bin/env python
"""对正在运行的 Requirement Agent 做聊天输入矩阵验收。

不会调用审核接口，因而不会创建正式需求或版本；但会创建会话、聊天消息和分析 run。
示例：
  python scripts/verify_agent_input_matrix.py --base-url http://127.0.0.1:8888 \
    --token "$API_AUTH_TOKEN"
  python scripts/verify_agent_input_matrix.py --case clarify --strict
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class StreamResult:
    events: list[tuple[str, dict[str, Any]]]

    def last(self, name: str) -> dict[str, Any] | None:
        return next((payload for event, payload in reversed(self.events) if event == name), None)

    @property
    def session_id(self) -> str | None:
        return (self.last("session") or {}).get("session_id")

    @property
    def run_id(self) -> str | None:
        return (self.last("done") or {}).get("run_id") or (self.last("session") or {}).get("run_id")


def parse_sse(response: httpx.Response) -> StreamResult:
    events: list[tuple[str, dict[str, Any]]] = []
    name: str | None = None
    for line in response.iter_lines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name:
            try:
                events.append((name, json.loads(line[6:])))
            except json.JSONDecodeError:
                events.append((name, {"_raw": line[6:]}))
        elif not line:
            name = None
    return StreamResult(events)


def stream_json(client: httpx.Client, path: str, payload: dict[str, Any]) -> StreamResult:
    with client.stream("POST", path, json=payload, timeout=180) as response:
        response.raise_for_status()
        return parse_sse(response)


def stream_file(client: httpx.Client, payload: dict[str, Any]) -> StreamResult:
    files = [("files", ("agent-matrix.txt", "新增库存预警：库存低于安全线时通知仓库主管。", "text/plain"))]
    with client.stream("POST", "/api/v1/agent/chat/stream-with-files", data=payload, files=files, timeout=180) as response:
        response.raise_for_status()
        return parse_sse(response)


def compact(result: StreamResult) -> dict[str, Any]:
    artifacts = (result.last("artifacts") or {}).get("artifacts") or {}
    routing = artifacts.get("routing") or {}
    return {
        "session_id": result.session_id,
        "run_id": result.run_id,
        "events": [name for name, _ in result.events],
        "steps": [payload.get("step") for name, payload in result.events if name == "step"],
        "narrative": "".join(
            str(payload.get("t") or "") for name, payload in result.events if name == "narrative"
        ),
        "done": result.last("done"),
        "routing": routing,
    }


def evidence(client: httpx.Client, run_id: str | None) -> dict[str, Any]:
    if not run_id:
        return {"run": None, "events": []}
    run = client.get(f"/api/v1/agent/runs/{run_id}").json()
    events = client.get(f"/api/v1/agent/runs/{run_id}/events").json().get("items", [])
    return {
        "run": {key: run.get(key) for key in ("run_id", "status", "stage", "checkpoint", "meta")},
        "events": [
            {key: item.get(key) for key in ("sequence", "event_type", "node", "payload")}
            for item in events if item.get("event_type") in {"route_selected", "node_started", "node_completed", "progress"}
        ],
    }


def run_case(client: httpx.Client, name: str) -> tuple[dict[str, Any], bool]:
    key = f"matrix-{name}-{uuid.uuid4().hex}"
    if name == "casual":
        result = stream_json(client, "/api/v1/agent/chat/stream", {"message": "你好", "client_message_id": key})
        return compact(result), not bool(result.run_id)
    if name == "question":
        result = stream_json(client, "/api/v1/agent/chat/stream", {"message": "你用的是什么模型？", "client_message_id": key})
        return compact(result), bool(result.last("done"))
    if name == "complete":
        text = "新增运营报表导出：运营主管可按部门和日期筛选订单并导出 XLSX；验收为 5 秒内导出 10 万行。"
        result = stream_json(client, "/api/v1/agent/chat/stream", {"message": text, "client_message_id": key})
        report = compact(result); report["database_evidence"] = evidence(client, result.run_id)
        return report, bool(result.run_id) and "artifacts" in report["events"]
    if name == "clarify":
        first = stream_json(client, "/api/v1/agent/chat/stream", {"message": "新增导出", "client_message_id": key})
        session_id = first.session_id
        second = stream_json(client, "/api/v1/agent/chat/stream", {
            "message": "给运营主管使用，按部门筛选订单，导出 XLSX，验收为 5 秒内完成。",
            "session_id": session_id, "client_message_id": key + "-supplement",
        })
        report = {"first": compact(first), "second": compact(second), "first_db": evidence(client, first.run_id), "second_db": evidence(client, second.run_id)}
        return report, (first.last("done") or {}).get("status") == "waiting_clarification"
    if name == "idempotent":
        payload = {"message": "新增客户标签筛选并导出 CSV，验收为可按地区筛选。", "client_message_id": key}
        first = stream_json(client, "/api/v1/agent/chat/stream", payload)
        # 幂等键的作用域是 (conversation_id, client_message_id)，重放必须带回同一会话。
        second = stream_json(client, "/api/v1/agent/chat/stream", {**payload, "session_id": first.session_id})
        report = {"first": compact(first), "replay": compact(second)}
        return report, first.run_id == second.run_id
    if name == "file":
        result = stream_file(client, {"message": "请按附件整理需求并检查重复", "client_message_id": key})
        report = compact(result); report["database_evidence"] = evidence(client, result.run_id)
        return report, bool(result.run_id) and "artifacts" in report["events"]
    raise ValueError(name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("AGENT_BASE_URL", "http://127.0.0.1:8888"))
    parser.add_argument("--token", default=os.getenv("API_AUTH_TOKEN", ""))
    parser.add_argument("--case", action="append", choices=["casual", "question", "clarify", "complete", "idempotent", "file"])
    parser.add_argument("--strict", action="store_true", help="任一场景不满足基本契约即以 1 退出")
    args = parser.parse_args()
    if not args.token:
        parser.error("provide --token or API_AUTH_TOKEN")
    headers = {"Authorization": f"Bearer {args.token}", "Accept": "text/event-stream"}
    passed = True
    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers) as client:
        for name in args.case or ["casual", "question", "clarify", "complete", "idempotent", "file"]:
            try:
                report, ok = run_case(client, name)
            except Exception as exc:  # 网络、模型和数据库问题都应在报告中可见
                report, ok = {"error": f"{type(exc).__name__}: {exc}"}, False
            passed = passed and ok
            print(json.dumps({"case": name, "passed": ok, "report": report}, ensure_ascii=False, indent=2, default=str))
    return 0 if passed or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
