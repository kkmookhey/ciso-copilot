"""Cross-domain detector: scan .github/workflows/*.{yml,yaml} for AWS OIDC
``role-to-assume`` references and emit
``github_repo → deploys_to → aws_iam_role`` edges.

The target IAM-role entity may not yet exist in the database (if the cloud
scanner hasn't run yet); unified_writer creates a stub on-the-fly. Once
the cloud scanner enumerates IAM roles, the stub gets hydrated with real
attributes.

Confidence: ``medium`` — the role ARN provides strong identity binding,
but we don't validate that the role actually exists in the customer's
AWS account (the cross-tenant Lambda doesn't have the credentials).
"""
from __future__ import annotations

import re

from detectors.base import EdgeEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.crossdomain"
detector_version = "0.1.0"

ROLE_RE = re.compile(
    r'role-to-assume\s*:\s*["\']?(arn:aws:iam::\d+:role/[A-Za-z0-9+=,.@_\-]+)["\']?',
    re.IGNORECASE,
)


def detect(ctx) -> DetectorResult:
    edges: list[EdgeEmission] = []
    repo_natural_key = f"github.com/{ctx.repo_full_name}"

    workflows_dir = ctx.repo_workdir / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return DetectorResult()

    files = sorted([p for p in workflows_dir.iterdir()
                    if p.is_file() and p.suffix in (".yml", ".yaml")])
    seen: set[str] = set()
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        rel_path = str(f.relative_to(ctx.repo_workdir))
        for m in ROLE_RE.finditer(text):
            role_arn = m.group(1)
            if role_arn in seen:
                continue
            seen.add(role_arn)
            line_no = text[:m.start()].count("\n") + 1
            snippet = text.splitlines()[line_no - 1].strip() if 0 < line_no <= len(text.splitlines()) else ""
            packet = ev.build(
                detector_id=detector_id, detector_version=detector_version,
                subject_kind="ai_relationship", subject_type="deploys_to",
                subject_name=f"repo→deploys_to→{role_arn}",
                source_events=[{
                    "kind": "file", "repo": ctx.repo_full_name,
                    "commit_sha": ctx.head_commit_sha,
                    "path": rel_path,
                    "snippet_lines": [line_no, line_no],
                    "snippet": snippet,
                }],
                reasoning_chain=[f"GitHub Actions workflow assumes {role_arn}"],
                confidence="medium",
            )
            edges.append(EdgeEmission(
                tenant_id=ctx.tenant_id,
                source_kind="github_repo", source_natural_key=repo_natural_key,
                target_kind="aws_iam_role", target_natural_key=role_arn,
                kind="deploys_to",
                attributes={"role_arn": role_arn, "workflow_path": rel_path},
                evidence_packet=packet,
                detector_id=detector_id, detector_version=detector_version,
            ))

    return DetectorResult(entities=[], edges=edges, findings=[])
