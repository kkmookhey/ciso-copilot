"""Detect MCP servers declared in a repo.

Two signals:
  1. Python source: ``from mcp.server import Server`` + a ``Server("...")``
     constructor + ``@<var>.list_tools()`` decorated function returning a
     list of ``{"name": "...", ...}`` dicts.
  2. Config file: ``mcp.json`` or ``claude_desktop_config.json`` with an
     ``mcpServers`` mapping.

Emits one ``mcp_server`` asset per server, one ``tool`` asset per declared
tool (Python path only — config files don't list tools), a
``repository→deploys→mcp_server`` relationship per server, an
``mcp_server→invokes→tool`` relationship per tool, and an
``mcp_with_broad_perms`` finding (HIGH) when any tool name matches a
write-scope heuristic (``create_``, ``delete_``, ``write_``, ``update_``).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from detectors.base import AssetEmission, RelEmission, FindingEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.mcp_server"
detector_version = "0.1.0"

WRITE_SCOPE_PREFIXES = ("create_", "delete_", "write_", "update_")


def detect(ctx) -> DetectorResult:
    assets: list[AssetEmission] = []
    rels:   list[RelEmission] = []
    findings: list[FindingEmission] = []
    repo_ref = f"repository::::{ctx.repo_asset_id}"

    for py in sorted(ctx.repo_workdir.rglob("*.py")):
        try:
            text = py.read_text(errors="ignore")
        except OSError:
            continue
        if "mcp.server" not in text:
            continue
        rel_path = str(py.relative_to(ctx.repo_workdir))
        _emit_from_python(ctx, py, text, rel_path, repo_ref, assets, rels, findings)

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
        _emit_from_config(ctx, parsed, rel_path, repo_ref, assets, rels)

    return DetectorResult(assets=assets, relationships=rels, findings=findings)


def _emit_from_python(ctx, py: Path, text: str, rel_path: str, repo_ref: str,
                       assets: list, rels: list, findings: list) -> None:
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
    assets.append(AssetEmission(
        tenant_id=ctx.tenant_id, connection_id=ctx.connection_id,
        asset_type="mcp_server", name=server_name,
        source_repo_id=ctx.repo_asset_id, source_path=rel_path,
        attributes={"runtime": "python"},
        evidence_packet=server_packet,
        detector_id=detector_id, detector_version=detector_version,
    ))
    server_ref = f"mcp_server::{ctx.repo_asset_id}::{rel_path}::{server_name}"

    deploys_packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_relationship", subject_type="deploys",
        subject_name=f"repo→deploys→{server_name}",
        source_events=[], reasoning_chain=["mcp_server detected in repo"],
        confidence="high",
    )
    rels.append(RelEmission(
        tenant_id=ctx.tenant_id,
        source_asset_ref=repo_ref, target_asset_ref=server_ref,
        relationship_type="deploys",
        attributes={}, evidence_packet=deploys_packet,
        detector_id=detector_id, detector_version=detector_version,
    ))

    tool_names = _extract_tool_names(tree, server_var)
    broad_tools: list[str] = []
    for tool_name, tool_line in tool_names:
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
        assets.append(AssetEmission(
            tenant_id=ctx.tenant_id, connection_id=ctx.connection_id,
            asset_type="tool", name=tool_name,
            source_repo_id=ctx.repo_asset_id, source_path=rel_path,
            attributes={"mcp_server": server_name},
            evidence_packet=tool_packet,
            detector_id=detector_id, detector_version=detector_version,
        ))
        invokes_packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_relationship", subject_type="invokes",
            subject_name=f"{server_name}→invokes→{tool_name}",
            source_events=[], reasoning_chain=["tool declared in mcp_server list_tools"],
            confidence="high",
        )
        rels.append(RelEmission(
            tenant_id=ctx.tenant_id,
            source_asset_ref=server_ref,
            target_asset_ref=f"tool::{ctx.repo_asset_id}::{rel_path}::{tool_name}",
            relationship_type="invokes",
            attributes={}, evidence_packet=invokes_packet,
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
            subject_type="ai_asset",
            subject_ref=server_ref,
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


def _emit_from_config(ctx, parsed: object, rel_path: str, repo_ref: str,
                      assets: list, rels: list) -> None:
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
        server_ref = f"mcp_server::{ctx.repo_asset_id}::{rel_path}::{name}"
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
        assets.append(AssetEmission(
            tenant_id=ctx.tenant_id, connection_id=ctx.connection_id,
            asset_type="mcp_server", name=name,
            source_repo_id=ctx.repo_asset_id, source_path=rel_path,
            attributes=attributes,
            evidence_packet=server_packet,
            detector_id=detector_id, detector_version=detector_version,
        ))
        deploys_packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="ai_relationship", subject_type="deploys",
            subject_name=f"repo→deploys→{name}",
            source_events=[], reasoning_chain=["mcp_server declared in config"],
            confidence="high",
        )
        rels.append(RelEmission(
            tenant_id=ctx.tenant_id,
            source_asset_ref=repo_ref, target_asset_ref=server_ref,
            relationship_type="deploys",
            attributes={}, evidence_packet=deploys_packet,
            detector_id=detector_id, detector_version=detector_version,
        ))
