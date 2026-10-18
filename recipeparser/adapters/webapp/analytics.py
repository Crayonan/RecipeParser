"""
recipeparser/adapters/webapp/analytics.py — numbers about a run, and about runs side by side.

Everything is computed from the rows the run left in the store (runs, events,
api_calls, errors, recipes), so the numbers for a finished run never change
and a run can be compared with any other run, including runs of other books.
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from recipeparser.adapters.webapp.store import Store


def _pct(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def run_metrics(store: Store, run_id: str, prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    start = run.get("started_at") or run["created_at"]
    end = run.get("ended_at") or time.time()
    duration = max(0.0, end - start)

    calls = store.query(
        "SELECT ts, chunk, stage, kind, model, duration_ms, ok, status_code, status, prompt_tokens, output_tokens, "
        "thought_tokens, total_tokens, finish_reason FROM api_calls WHERE run_id = ? ORDER BY id",
        (run_id,),
    )
    ok_calls = [c for c in calls if c["ok"]]
    failed = [c for c in calls if not c["ok"]]
    latencies = [_num(c["duration_ms"]) for c in ok_calls]

    by_stage: Dict[str, Dict[str, Any]] = {}
    for stage, group in _group(calls, lambda c: c["stage"] or "—").items():
        lat = [_num(c["duration_ms"]) for c in group if c["ok"]]
        by_stage[stage] = {
            "calls": len(group),
            "failed": sum(1 for c in group if not c["ok"]),
            "avg_ms": round(sum(lat) / len(lat), 1) if lat else None,
            "p50_ms": _pct(lat, 0.5),
            "p95_ms": _pct(lat, 0.95),
            "prompt_tokens": int(sum(_num(c["prompt_tokens"]) for c in group)),
            "output_tokens": int(sum(_num(c["output_tokens"]) for c in group)),
        }

    status_codes = Counter(str(c["status_code"] or c["status"] or "?") for c in calls)
    finish_reasons = Counter(c["finish_reason"] for c in ok_calls if c["finish_reason"])

    tokens = {
        "prompt": int(sum(_num(c["prompt_tokens"]) for c in calls)),
        "output": int(sum(_num(c["output_tokens"]) for c in calls)),
        "thought": int(sum(_num(c["thought_tokens"]) for c in calls)),
        "total": int(sum(_num(c["total_tokens"]) for c in calls)),
    }
    cost = None
    if prices and (prices.get("input") or prices.get("output")):
        cost = round(
            tokens["prompt"] / 1e6 * prices.get("input", 0)
            + (tokens["output"] + tokens["thought"]) / 1e6 * prices.get("output", 0),
            4,
        )

    # Per-minute timeline: calls (ok / failed) and recipes.
    timeline: Dict[int, Dict[str, int]] = defaultdict(lambda: {"ok": 0, "failed": 0, "recipes": 0, "errors": 0})
    for c in calls:
        timeline[int((c["ts"] - start) // 60)]["ok" if c["ok"] else "failed"] += 1
    recipes = store.query(
        "SELECT ts, needs_review, review_json, embedding_json, categories_json FROM recipes WHERE run_id = ?", (run_id,)
    )
    for r in recipes:
        timeline[int((r["ts"] - start) // 60)]["recipes"] += 1
    errors = store.query("SELECT ts, severity, code, category, resolved FROM errors WHERE run_id = ?", (run_id,))
    for e in errors:
        timeline[int((e["ts"] - start) // 60)]["errors"] += 1
    minutes = max(timeline) + 1 if timeline else 0
    series = [
        {"minute": m, **timeline.get(m, {"ok": 0, "failed": 0, "recipes": 0, "errors": 0})} for m in range(minutes)
    ]
    peak_rpm = max((s["ok"] + s["failed"] for s in series), default=0)

    review_kinds: "Counter[str]" = Counter()
    for r in recipes:
        for n in json.loads(r["review_json"] or "[]"):
            review_kinds[n.get("kind", "note")] += 1
    categories: "Counter[str]" = Counter()
    for r in recipes:
        categories.update(json.loads(r["categories_json"] or "[]"))
    emb = [json.loads(r["embedding_json"]) for r in recipes if r["embedding_json"]]
    emb_dims = Counter(e.get("dims") for e in emb)
    norms = [_num(e.get("norm")) for e in emb if e.get("norm") is not None]

    chunk_states: "Counter[str]" = Counter()
    for ev in store.query("SELECT data_json FROM events WHERE run_id = ? AND kind = 'chunk.done'", (run_id,)):
        try:
            chunk_states[json.loads(ev["data_json"] or "{}").get("state", "done")] += 1
        except ValueError:
            pass
    total_chunks = int(run.get("chunks_total") or 0)
    not_reached = max(0, total_chunks - sum(chunk_states.values()))
    if not_reached:
        chunk_states["not processed"] = not_reached

    minutes_f = duration / 60 if duration else 0
    n_recipes = len(recipes)
    return {
        "run_id": run_id,
        "status": run["status"],
        "label": run.get("label"),
        "book": run["book_name"],
        "provider": run["provider"],
        "gen_model": run.get("gen_model"),
        "embed_model": run.get("embed_model"),
        "created_at": run["created_at"],
        "duration_secs": round(duration, 1),
        "chunks": {
            "in_book": run.get("chunks_in_book"),
            "selected": total_chunks,
            "done": run.get("chunks_done"),
            "states": dict(chunk_states),
        },
        "recipes": {
            "total": n_recipes,
            "needs_review": sum(1 for r in recipes if r["needs_review"]),
            "review_notes": dict(review_kinds),
            "per_chunk": round(n_recipes / run["chunks_done"], 2) if run.get("chunks_done") else None,
            "per_minute": round(n_recipes / minutes_f, 2) if minutes_f else None,
            "categories": dict(categories.most_common(30)),
        },
        "api": {
            "calls": len(calls),
            "ok": len(ok_calls),
            "failed": len(failed),
            "failure_rate": round(len(failed) / len(calls), 4) if calls else 0,
            "avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "p50_ms": _pct(latencies, 0.5),
            "p95_ms": _pct(latencies, 0.95),
            "max_ms": round(max(latencies), 1) if latencies else None,
            "avg_rpm": round(len(calls) / minutes_f, 1) if minutes_f else None,
            "peak_rpm": peak_rpm,
            "by_stage": by_stage,
            "status_codes": dict(status_codes),
            "finish_reasons": dict(finish_reasons),
        },
        "tokens": tokens,
        "cost_usd": cost,
        "errors": {
            "errors": sum(1 for e in errors if e["severity"] in ("error", "fatal")),
            "warnings": sum(1 for e in errors if e["severity"] == "warning"),
            "unresolved": sum(1 for e in errors if not e["resolved"]),
            "by_code": dict(Counter(e["code"] for e in errors).most_common()),
            "by_category": dict(Counter(e["category"] for e in errors).most_common()),
        },
        "embeddings": {
            "count": len(emb),
            "dims": {str(k): v for k, v in emb_dims.items()},
            "norm_min": round(min(norms), 6) if norms else None,
            "norm_max": round(max(norms), 6) if norms else None,
            "norm_avg": round(sum(norms) / len(norms), 6) if norms else None,
        },
        "timeline": series,
        "options": run.get("options") or {},
    }


def _group(rows: List[Dict[str, Any]], key: Any) -> Dict[Any, List[Dict[str, Any]]]:
    out: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return out


COMPARE_ROWS = [
    ("Status", "status"),
    ("Book", "book"),
    ("Provider", "provider"),
    ("Generation model", "gen_model"),
    ("Embedding model", "embed_model"),
    ("Duration (s)", "duration_secs"),
    ("Chunks selected", "chunks.selected"),
    ("Chunks done", "chunks.done"),
    ("Recipes", "recipes.total"),
    ("Recipes needing review", "recipes.needs_review"),
    ("Recipes per chunk", "recipes.per_chunk"),
    ("Recipes per minute", "recipes.per_minute"),
    ("API calls", "api.calls"),
    ("Failed calls", "api.failed"),
    ("Failure rate", "api.failure_rate"),
    ("Avg latency (ms)", "api.avg_ms"),
    ("p95 latency (ms)", "api.p95_ms"),
    ("Avg requests/min", "api.avg_rpm"),
    ("Peak requests/min", "api.peak_rpm"),
    ("Prompt tokens", "tokens.prompt"),
    ("Output tokens", "tokens.output"),
    ("Thinking tokens", "tokens.thought"),
    ("Total tokens", "tokens.total"),
    ("Est. cost (USD)", "cost_usd"),
    ("Errors", "errors.errors"),
    ("Warnings", "errors.warnings"),
    ("Concurrency", "options.concurrency"),
    ("Requests/min limit", "options.rpm"),
    ("Recipe limit", "options.max_recipes"),
    ("% of book", "options.book_percent"),
]


def _get(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def compare(store: Store, run_ids: List[str], prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    metrics = [run_metrics(store, rid, prices) for rid in run_ids]
    rows = [{"label": label, "key": key, "values": [_get(m, key) for m in metrics]} for label, key in COMPARE_ROWS]

    runs = [store.get_run(rid) or {} for rid in run_ids]
    settings_keys = sorted({k for r in runs for k in (r.get("settings") or {})})
    settings_diff = []
    for key in settings_keys:
        values = [((r.get("settings") or {}).get(key) or {}).get("value") for r in runs]
        if len({json.dumps(v) for v in values}) > 1:
            settings_diff.append({"key": key, "values": values})
    prompt_names = sorted({k for r in runs for k in (r.get("prompts") or {})})
    prompts_diff = [
        {
            "name": n,
            "edited": [n in (r.get("prompts") or {}) for r in runs],
            "same": len({(r.get("prompts") or {}).get(n) for r in runs}) == 1,
        }
        for n in prompt_names
    ]
    error_codes = sorted({c for m in metrics for c in m["errors"]["by_code"]})
    errors_matrix = [{"code": c, "values": [m["errors"]["by_code"].get(c, 0) for m in metrics]} for c in error_codes]

    titles = [
        {
            r["title"].strip().lower(): r["title"]
            for r in store.query("SELECT title FROM recipes WHERE run_id = ?", (rid,))
        }
        for rid in run_ids
    ]
    all_keys = set().union(*[set(t) for t in titles]) if titles else set()
    common = set.intersection(*[set(t) for t in titles]) if titles else set()
    only = [
        sorted(t[k] for k in set(t) - set().union(*[set(o) for j, o in enumerate(titles) if j != i]))
        for i, t in enumerate(titles)
    ]
    return {
        "runs": [
            {
                "id": m["run_id"],
                "label": m["label"],
                "book": m["book"],
                "status": m["status"],
                "created_at": m["created_at"],
            }
            for m in metrics
        ],
        "rows": rows,
        "settings_diff": settings_diff,
        "prompts_diff": prompts_diff,
        "errors_by_code": errors_matrix,
        "recipes": {"union": len(all_keys), "common": len(common), "only": only},
        "timelines": [m["timeline"] for m in metrics],
    }
