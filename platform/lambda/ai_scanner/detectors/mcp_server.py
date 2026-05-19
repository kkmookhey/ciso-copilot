"""Detect MCP servers declared in a repo.

Two signals:
  1. Python source: ``from mcp.server import Server`` + a ``Server("...")``
     constructor + ``@<var>.list_tools()`` decorated function returning a
     list of ``{"name": "...", ...}`` dicts.
  2. Config file: ``mcp.json`` or ``claude_desktop_config.json`` with an
     ``mcpServers`` mapping.

Emits one ``ai_mcp_server`` entity per server, one ``ai_tool`` entity per
declared tool (Python path only — config files don't list tools), a
``github_repo → deploys → ai_mcp_server`` edge per server, an
``ai_mcp_server → invokes → ai_tool`` edge per tool, and an
``mcp_with_broad_perms`` finding (HIGH) when any tool name matches a
write-scope heuristic (``create_``, ``delete_``, ``write_``, ``update_``).

SP1 natural-key shape (per-file, no cross-file dedup — two repos with the
same MCP server name are distinct entities, and a repo with two files each
declaring a server with the same name produces two entities):
  - ai_mcp_server: ``f"{repo_nk}::{rel_path}::{server_name}"``
  - ai_tool:       ``f"{repo_nk}::{rel_path}::{tool_name}"``
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from detectors.base import EntityEmission, EdgeEmission, FindingEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.mcp_server"
detector_version = "0.2.0"

WRITE_SCOPE_PREFIXES = ("create_", "delete_", "write_", "update_")


def detect(ctx) -> DetectorResult:
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission] = []
    findings: list[FindingEmission] = []
    repo_nk = f"github.com/{ctx.repo_full_name}"

    for py in sorted(ctx.repo_workdir.rglob("*.py")):
        try:
            text = py.read_text(errors="ignore")
        except OSError:
            continue
        if "mcp.server" not in text:
            continue
        rel_path = str(py.relative_to(ctx.repo_workdir))
        _emit_from_python(ctx, py, text, rel_path, repo_nk, entities, edges, findings)

    config_names = ("mcp.json", "claude_desktop_config.json")
    config_files = sorted(
        p for name in config_names for p in ctx.repo_workdir.rglob(name)
    )
    for cfg in config_files:
        try:
            text = cfg.read_text(errors="ignore")
            parsed = json.loads(text)
        except (OSError, json.JSONDecodeError):
            continue
        rel_path = str(cfg.relative_to(ctx.repo_workdir))
        _emit_from_config(ctx, parsed, rel_path, repo_nk, entities, edges)

    return DetectorResult(entities=entities, edges=edges, findings=findings)


def _emit_from_python(ctx, py: Path, text: str, rel_path: str, repo_nk: str,
                       entities: list, edges: list, findings: list) -> None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return

    server_name: str | None = None
    server_line: int | None = None
    server_var: str | None = None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            func = call.func
            if (isinstance(func, ast.Name) and func.id == "Server") or \
               (isinstance(func, ast.Attribute) and func.attr == "Server"):
                if call.args and isinstance(call.args[0], ast.Constant) \
                        and isinstance(call.args[0].value, str):
                    server_name = call.args[0].value
                    server_line = node.lineno
                    if node.targets and isinstance(node.targets[0], ast.Name):
                        server_var = node.targets[0].id
                    break

    if not server_name:
        return

    server_nk = f"{repo_nk}::{rel_path}::{server_name}"

    server_packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_asset", subject_type="mcp_server", subject_name=server_name,
        source_events=[{
            "kind": "file", "repo": ctx.repo_full_name,
            "commit_sha": ctx.head_commit_sha,
            "path": rel_path, "snippet_lines": [server_line, server_line],
            "snippet": text.splitlines()[server_line - 1] if server_line else "",
        }],
        reasoning_chain=[f"matched Server(\"{server_name}\") at {rel_path}:{server_line}"],
        confidence="high",
    )
    entities.append(EntityEmission(
        tenant_id=ctx.tenant_id, kind="ai_mcp_server",
        natural_key=server_nk, display_name=server_name, domain="ai",
        attributes={"runtime": "python"},
        evidence_packet=server_packet,
        detector_id=detector_id, detector_version=detector_version,
        connection_id=ctx.connection_id, source_path=rel_path,
    ))

    deploys_packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_relationship", subject_type="deploys",
        subject_name=f"repo→deploys→{server_name}",
        source_events=[], reasoning_chain=["mcp_server detected in repo"],
        confidence="high",
    )
    edges.append(EdgeEmission(
        tenant_id=ctx.tenant_id,
        source_kind="github_repo", source_natural_key=repo_nk,
        target_kind="ai_mcp_server", target_natural_key=server_nk,
        kind="deploys", attributes={}, evidence_packet=deploys_packet,
        detector_id=detector_id, detector_version=detector_version,
    ))

    tool_names = _extract_tool_names(tree, server_var)
    broad_tools: list[str] = []
    for tool_name, tool_line in tool_names:
        tool_nk = f"{repo_nk}::{rel_path}::{tool_name}"
        tool_packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_asset", subject_type="tool", subject_name=tool_name,
            source_events=[{
                "kind": "file", "repo": ctx.repo_full_name,
                "commit_sha": ctx.head_commit_sha,
                "path": rel_path, "snippet_lines": [tool_line, tool_line],
                "snippet": text.splitlines()[tool_line - 1] if tool_line else "",
            }],
            reasoning_chain=[f"matched tool name \"{tool_name}\" in list_tools at {rel_path}:{tool_line}"],
            confidence="high",
        )
        entities.append(EntityEmission(
            tenant_id=ctx.tenant_id, kind="ai_tool",
            natural_key=tool_nk, display_name=tool_name, domain="ai",
            attributes={"mcp_server": server_name},
            evidence_packet=tool_packet,
            detector_id=detector_id, detector_version=detector_version,
            connection_id=ctx.connection_id, source_path=rel_path,
        ))
        invokes_packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_relationship", subject_type="invokes",
            subject_name=f"{server_name}→invokes→{tool_name}",
            source_events=[], reasoning_chain=["tool declared in mcp_server list_tools"],
            confidence="high",
        )
        edges.append(EdgeEmission(
            tenant_id=ctx.tenant_id,
            source_kind="ai_mcp_server", source_natural_key=server_nk,
            target_kind="ai_tool", target_natural_key=tool_nk,
            kind="invokes", attributes={}, evidence_packet=invokes_packet,
            detector_id=detector_id, detector_version=detector_version,
        ))
        if tool_name.startswith(WRITE_SCOPE_PREFIXES):
            broad_tools.append(tool_name)

    if broad_tools:
        finding_packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="finding", subject_type="mcp_with_broad_perms",
            subject_name=server_name,
            source_events=[{
                "kind": "file", "repo": ctx.repo_full_name,
                "commit_sha": ctx.head_commit_sha,
                "path": rel_path, "snippet_lines": [server_line, server_line],
                "snippet": text.splitlines()[server_line - 1] if server_line else "",
            }],
            reasoning_chain=[
                f"MCP server {server_name} declares write-scope tools: {sorted(broad_tools)}"
            ],
            confidence="high",
        )
        findings.append(FindingEmission(
            tenant_id=ctx.tenant_id,
            finding_type="mcp_with_broad_perms",
            severity="high",
            title=f"MCP server '{server_name}' declares write-scope tools",
            description=(
                f"MCP server {server_name} exposes tools with names suggesting "
                f"write or mutating scope: {sorted(broad_tools)}. Review whether "
                "the host process running this server should have these capabilities."
            ),
            subject_entity_kind="ai_mcp_server",
            subject_entity_natural_key=server_nk,
            subject_type=None,
            subject_ref=None,
            evidence_packet=finding_packet,
            confidence="high",
        ))


def _extract_tool_names(tree: ast.AST, server_var: str | None) -> list[tuple[str, int]]:
    """Find functions decorated with @<server>.list_tools() and pull tool names
    from their returned list-of-dicts literal."""
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _has_list_tools_decorator(node, server_var):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.List):
                for elt in sub.value.elts:
                    if not isinstance(elt, ast.Dict):
                        continue
                    for k, v in zip(elt.keys, elt.values):
                        if (isinstance(k, ast.Constant) and k.value == "name"
                                and isinstance(v, ast.Constant)
                                and isinstance(v.value, str)):
                            out.append((v.value, v.lineno))
                            break
    return out


def _has_list_tools_decorator(fn: ast.FunctionDef | ast.AsyncFunctionDef,
                               server_var: str | None) -> bool:
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr == "list_tools":
            if server_var is None:
                return True
            if isinstance(target.value, ast.Name) and target.value.id == server_var:
                return True
    return False


def _emit_from_config(ctx, parsed: object, rel_path: str, repo_nk: str,
                      entities: list, edges: list) -> None:
    if not isinstance(parsed, dict):
        return
    servers = parsed.get("mcpServers")
    if not isinstance(servers, dict):
        return
    for name in sorted(servers.keys()):
        cfg = servers[name]
        attributes: dict = {"runtime": "config"}
        if isinstance(cfg, dict):
            command = cfg.get("command")
            if isinstance(command, str):
                attributes["command"] = command
        server_nk = f"{repo_nk}::{rel_path}::{name}"
        server_packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_asset", subject_type="mcp_server", subject_name=name,
            source_events=[{
                "kind": "file", "repo": ctx.repo_full_name,
                "commit_sha": ctx.head_commit_sha,
                "path": rel_path, "snippet_lines": [1, 1],
                "snippet": f"mcpServers.{name}",
            }],
            reasoning_chain=[f"declared in {rel_path} mcpServers.{name}"],
            confidence="high",
        )
        entities.append(EntityEmission(
            tenant_id=ctx.tenant_id, kind="ai_mcp_server",
            natural_key=server_nk, display_name=name, domain="ai",
            attributes=attributes,
            evidence_packet=server_packet,
            detector_id=detector_id, detector_version=detector_version,
            connection_id=ctx.connection_id, source_path=rel_path,
        ))
        deploys_packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_relationship", subject_type="deploys",
            subject_name=f"repo→deploys→{name}",
            source_events=[], reasoning_chain=["mcp_server declared in config"],
            confidence="high",
        )
        edges.append(EdgeEmission(
            tenant_id=ctx.tenant_id,
            source_kind="github_repo", source_natural_key=repo_nk,
            target_kind="ai_mcp_server", target_natural_key=server_nk,
            kind="deploys", attributes={}, evidence_packet=deploys_packet,
            detector_id=detector_id, detector_version=detector_version,
        ))
