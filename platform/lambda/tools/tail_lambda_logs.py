# platform/lambda/tools/tail_lambda_logs.py
"""Search a Lambda's CloudWatch logs for a regex over a recent time window."""
from __future__ import annotations
import time
from datetime import datetime, timezone

import boto3
from tools.main import register


_logs = boto3.client("logs")


@register("tail_lambda_logs_for_pattern")
def handle(args: dict, claims: dict) -> dict:
    fn_name = args["function_name"]
    regex   = args["regex"]
    hours   = int(args.get("window_hours", 72))

    end_ts   = int(datetime.now(timezone.utc).timestamp())
    start_ts = end_ts - (hours * 3600)
    log_group = f"/aws/lambda/{fn_name}"

    # Escape forward slashes so an LLM-supplied regex can't break out of the
    # Insights /.../ delimiter and inject extra query clauses.
    safe_regex = regex.replace("/", r"\/")
    insights_query = (
        f"fields @timestamp, @message | "
        f"filter @message like /{safe_regex}/ | "
        f"sort @timestamp desc | limit 100"
    )

    start = _logs.start_query(
        logGroupName=log_group,
        startTime=start_ts, endTime=end_ts,
        queryString=insights_query,
    )
    qid = start["queryId"]

    # Poll up to 30 seconds.
    rs = None
    for _ in range(30):
        time.sleep(1)
        rs = _logs.get_query_results(queryId=qid)
        if rs["status"] == "Complete":
            break
    else:
        return {
            "matches":   [],
            "reason":    "query_timeout",
            "speakable": f"Log query against {fn_name} timed out.",
        }

    matches = []
    for row in rs.get("results", []):
        d = {c["field"]: c["value"] for c in row}
        matches.append({"timestamp": d.get("@timestamp"), "message": d.get("@message", "")[:500]})

    if not matches:
        return {
            "matches":   [],
            "speakable": f"Nothing matching that pattern in {fn_name} over the last {hours} hours.",
        }
    return {
        "matches":   matches,
        "speakable": f"Found {len(matches)} matches in {fn_name} over the last {hours} hours.",
    }
