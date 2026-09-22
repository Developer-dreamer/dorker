from typing import Iterable

USER_SKILL_PROFILE: dict[str, float] = {
    "python": 0.8,
    "numpy": 0.5,
    "rest api": 1.0,
    "pandas": 0.5,
    "matplotlib": 0.4,
    "pytorch": 0.1,
    "go": 0.8,
    "cgo": 0.0,
    "postgresql": 0.8,
    "mongodb": 0.3,
    "redis": 0.5,
    "git": 0.8,
    "github": 0.7,
    "gitlab": 0.4,
    "gcp": 0.6,
    "aws": 0.0,
    "terraform": 0.6,
    "docker": 0.8,
    "c#/.net": 0.8,
    "asp.net core": 0.5,
    "entity framework core": 0.6,
    "c/c++": 0.4,
    "linux systems": 0.8,
    "opentelemetry": 0.3,
}

ALIAS_MAP: dict[str, str] = {
    "golang": "go",
    "postgres": "postgresql",
    "psql": "postgresql",
    "k8s": "orchestration",
    "kubernetes": "orchestration",
    "linux": "linux systems",
    "pion": "webrtc",
    "go-pion": "webrtc",
    "otel": "opentelemetry",
    "c#": "c#/.net",
    ".net": "c#/.net",
    "dotnet": "c#/.net",
    "c++": "c/c++",
    "cpp": "c/c++",
    "c": "c/c++",
    "google cloud": "gcp",
    "amazon web services": "aws",
}


def normalize_tool(raw_tool: str) -> str:
    cleaned = raw_tool.strip().lower()
    return ALIAS_MAP.get(cleaned, cleaned)


def compute_weighted_tversky_match(
    extracted_tools: Iterable[str],
    user_skills: dict[str, float] = USER_SKILL_PROFILE,
    beta: float = 1.0,
) -> dict[str, float | list[str]]:
    """
    Computes asymmetric Tversky-derived coverage of job secondary tools.

    alpha is implicitly 0 to avoid penalizing candidate for non-requested skills.
    beta controls penalty severity for missing/under-qualified requirements.
      - beta = 1.0: Linear coverage metric.
      - beta > 1.0: Aggressively penalizes missing tools.
      - beta < 1.0: Lenient towards missing tools.
    """
    # 1. Normalize and deduplicate extracted tools
    normalized_job_tools = {normalize_tool(t) for t in extracted_tools if t.strip()}

    if not normalized_job_tools:
        # If the job requires no secondary tools, deficit is 0
        return {
            "score": 1.0,
            "matched_tools": [],
            "missing_tools": [],
        }

    tp = 0.0
    fn = 0.0
    matched_tools: list[str] = []
    missing_tools: list[str] = []

    for tool in normalized_job_tools:
        weight = user_skills.get(tool, 0.0)
        tp += weight
        fn += 1.0 - weight

        if weight > 0.0:
            matched_tools.append(f"{tool} ({weight})")
        else:
            missing_tools.append(tool)

    denominator = tp + (beta * fn)
    score = (tp / denominator) if denominator > 0.0 else 0.0

    return {
        "score": round(score, 4),
        "tp": round(tp, 2),
        "fn": round(fn, 2),
        "matched_tools": sorted(matched_tools),
        "missing_tools": sorted(missing_tools),
    }


if __name__ == "__main__":
    eval_tools = ["Python", "Flask", "Redis", "Celery", "PostgreSQL", "MySQL", "Docker", "REST API"]

    res = compute_weighted_tversky_match(eval_tools)
    print(res)
