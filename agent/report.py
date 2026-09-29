"""Markdown shown in Flower Chat as the investigation runs (Spec §2).

Everything here renders what the coordinator can see: aggregates and the
message log. Nothing here can reach national data.
"""

from __future__ import annotations

from .audit import MessageLog
from .coordinator import Investigation
from .schemas import FinalAssessment


def header() -> str:
    return (
        "## Sovereign Outbreak Intelligence\n"
        "_Synthetic demonstration data. Queries move; sovereign data does not._\n\n"
        "Each national Ministry-of-Health node keeps its data on its own SuperNode. "
        "The regional coordinator can only send structured queries and receive "
        "aggregate answers.\n\n"
    )


def round_markdown(round_: str, inv: Investigation) -> str:
    if round_ == "1-scan":
        rows = [
            "### Round 1 — Syndrome scan sent to every national node\n",
            "| Country | Observations | Assessment | Malaria | Anomalies (demo strength) |",
            "|---|---:|---|---|---|",
        ]
        for country, scan in inv.scans.items():
            anomalies = ", ".join(
                f"{a.signal} {'↑' if a.direction == 'up' else '↓'} {a.strength:.2f}"
                for a in scan.anomalies
            )
            rows.append(
                f"| {country} | {scan.sample_size} | {scan.assessment} "
                f"| {scan.malaria_signal} | {anomalies or '—'} |"
            )
        for country in inv.refusals:
            rows.append(f"| {country} | — | refused | — | — |")
        rows.append("")
        for country, scan in inv.scans.items():
            rows.append(_agent_voice(country, scan.mode, scan.interpretation, scan.tools_used, scan.policy_notes))
        return "\n".join(rows) + "\n\n"

    if round_ == "2-compare":
        lines = ["### Round 2 — Coordinator compares aggregates (no messages)\n"]
        for (signal, direction), countries in inv.shared_signals.items():
            lines.append(f"- **{signal} {direction}** in {', '.join(countries)}")
        if not inv.shared_signals:
            lines.append("- No anomaly is shared by two or more countries.")
        return "\n".join(lines) + "\n\n"

    if round_ == "3-verify" and inv.follow_up:
        f = inv.follow_up
        chooser = "the coordinator's AI" if f.chosen_by == "model" else "the fixed rule"
        lines = [
            f"### Round 3 — One follow-up to {', '.join(f.asked)}\n",
            f"> {f.question}\n",
            f"_Chosen by {chooser}: {f.rationale}_\n",
            "| Country | Answer | Mode |",
            "|---|---|---|",
        ]
        lines += [f"| {c} | {a} | {f.modes.get(c, '—')} |" for c, a in f.answers.items()]
        lines.append("")
        lines += [f"> **{c}:** {text}\n" for c, text in f.interpretations.items()]
        return "\n".join(lines) + "\n\n"
    return ""


def _agent_voice(
    country: str, mode: str, interpretation: str, tools: list[str], policy: list[str]
) -> str:
    who = "AI agent" if mode == "agent" else "deterministic fallback"
    text = interpretation or "Answered with computed aggregates only."
    detail = f"tools: {', '.join(tools) or '—'}"
    if policy:
        detail += f" · policy: {' '.join(policy)}"
    return f"> **{country}** ({who}): {text}  \n> <sub>{detail}</sub>\n"


def assessment_markdown(a: FinalAssessment) -> str:
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {i}" for i in items) if items else "- None"

    return (
        "### Final assessment\n\n"
        f"**Status:** `{a.status}` · **Cross-border pattern:** "
        f"{'yes' if a.cross_border_pattern else 'no'} "
        f"({', '.join(a.countries_with_pattern) or '—'}) · "
        f"**Demo confidence:** {a.demo_confidence_score:.2f}\n\n"
        f"**Supporting evidence**\n{bullets(a.supporting_evidence)}\n\n"
        f"**Contradictory evidence**\n{bullets(a.contradictory_evidence)}\n\n"
        f"**Uncertainties**\n{bullets(a.uncertainties)}\n\n"
        f"**Recommended next step:** {a.recommended_next_step}\n\n"
    )


def audit_markdown(log: MessageLog) -> str:
    rows = [
        "### Federation message log\n",
        "| # | Round | Direction | Country | Kind | Bytes | Raw rows |",
        "|---:|---|---|---|---|---:|---:|",
    ]
    for i, e in enumerate(log.entries, 1):
        rows.append(
            f"| {i} | {e.round} | {e.direction} | {e.country} | {e.kind} "
            f"| {len(e.payload)} | {e.raw_rows} |"
        )
    return "\n".join(rows) + "\n\n"


def footer(a: FinalAssessment) -> str:
    return (
        "---\n"
        "### 🧑‍⚕️ Human epidemiologist review recommended\n\n"
        f"- Messages exchanged: **{a.messages_exchanged}**\n"
        f"- Aggregate result objects shared: **{a.aggregate_result_objects}**\n"
        f"- **Raw patient rows transferred: {a.raw_patient_rows_received}**\n\n"
        "_Synthetic demonstration data. Strengths and confidence are demonstration "
        "scores, not validated epidemiological probabilities._\n"
    )
