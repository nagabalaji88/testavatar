"""Consensus Synthesis Engine.

The engine turns N scored candidate prompts into one Elite Consensus Prompt
through four deterministic phases plus an optional LLM polish pass:

  1. EXTRACT   — parse every candidate into canonical sections and score each
                 section against the rubric metrics that section governs.
  2. RESOLVE   — detect syntactic (format/heading) and semantic (contradictory
                 directive, conflicting numeric limit) conflicts and resolve
                 them by weighted authority.
  3. MERGE     — adopt the strongest variant of each section as the base, then
                 graft in genuinely novel directives from the other models,
                 skipping near-duplicates.
  4. OPTIMIZE  — deduplicate, collapse whitespace, normalise headings and strip
                 filler to recover tokens without losing instructions.

Every phase is pure and testable; the LLM polish pass is additive and is
discarded if it drops a required section, so the engine never regresses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable, Optional, Sequence

from app.core.config import settings
from app.core.logging import get_logger
from app.models.schemas import (
    ConflictRecord,
    JudgeVerdict,
    OptimizationReport,
    RequirementAnalysis,
    SectionProvenance,
)
from app.services.llm_service import (
    LLMError,
    LLMResult,
    LLMService,
    estimate_tokens,
    llm_service,
)
from app.services.model_registry import UnknownProviderError, model_registry

logger = get_logger(__name__)

# --- Canonical section taxonomy -------------------------------------------

CANONICAL_SECTIONS: list[tuple[str, str]] = [
    ("role", "Role & Objective"),
    ("context", "Context"),
    ("instructions", "Instructions"),
    ("reasoning", "Reasoning Process"),
    ("output", "Output Format"),
    ("constraints", "Constraints & Guardrails"),
    ("security", "Security & Safety"),
    ("failure", "Failure Handling"),
    ("examples", "Examples"),
    ("additional", "Additional Guidance"),
]
CANONICAL_TITLES: dict[str, str] = dict(CANONICAL_SECTIONS)
CANONICAL_ORDER: list[str] = [key for key, _ in CANONICAL_SECTIONS]

SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "role": ("role", "persona", "identity", "objective", "mission", "you are", "purpose"),
    "context": ("context", "background", "domain", "situation", "audience", "inputs"),
    "instructions": ("instruction", "task", "directive", "responsibilit", "workflow", "procedure", "steps"),
    "reasoning": ("reasoning", "thinking", "analysis process", "chain of thought", "approach", "method"),
    "output": ("output", "response format", "format", "schema", "deliverable", "structure of"),
    "constraints": ("constraint", "rule", "restriction", "boundar", "requirement", "policy"),
    "security": ("security", "safety", "guardrail", "injection", "privacy", "compliance"),
    "failure": ("failure", "fallback", "error", "edge case", "escalation", "exception", "unknown"),
    "examples": ("example", "few-shot", "sample", "illustration", "demonstration"),
}

# Which rubric metrics express the quality of each canonical section.
SECTION_METRICS: dict[str, tuple[str, ...]] = {
    "role": ("role_definition", "instruction_clarity"),
    "context": ("context_awareness", "hallucination_prevention"),
    "instructions": ("instruction_clarity", "constraints_completeness", "determinism"),
    "reasoning": ("reasoning_quality", "adaptability"),
    "output": ("output_formatting", "determinism", "tool_calling_accuracy"),
    "constraints": ("constraints_completeness", "instruction_clarity"),
    "security": ("security_guardrails", "hallucination_prevention"),
    "failure": ("adaptability", "tool_calling_accuracy"),
    "examples": ("example_quality", "output_formatting"),
    "additional": ("maintainability", "token_efficiency"),
}

HEADING_RE = re.compile(r"^\s{0,3}(?P<hashes>#{1,4})\s+(?P<title>.+?)\s*$")
NEGATION_RE = re.compile(
    r"\b(do not|don't|never|must not|cannot|avoid|refuse|prohibited|forbidden|no longer)\b",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
FORMAT_TOKENS = ("json", "xml", "yaml", "markdown", "csv", "plain text")
FILLER_PATTERNS = [
    re.compile(r"^\s*(certainly|sure|of course|here is|here's)\b.*$", re.IGNORECASE),
    re.compile(r"^\s*(i hope this helps|let me know if).*$", re.IGNORECASE),
    re.compile(r"^\s*(note that it is important to note that)\s*", re.IGNORECASE),
]
STOPWORDS = frozenset(
    """a an the and or of to in for on with by is are be as that this it its your you
    must should will can may from at into than then when where which who whom while""".split()
)

SIMILARITY_DUPLICATE = 0.86
SIMILARITY_CONFLICT = 0.55


# --- Data structures -------------------------------------------------------


@dataclass
class CandidateInput:
    """A scored candidate entering the consensus engine."""

    model_id: str
    model_name: str
    content: str
    verdict: JudgeVerdict
    weight: float = 1.0

    @property
    def overall(self) -> float:
        return self.verdict.overall_score


@dataclass
class SectionVariant:
    model_id: str
    model_name: str
    canonical: str
    title: str
    body: str
    units: list[str]
    score: float

    @property
    def is_empty(self) -> bool:
        return not self.body.strip()


@dataclass
class MergedSection:
    canonical: str
    title: str
    body: str
    provenance: SectionProvenance
    conflicts: list[ConflictRecord] = field(default_factory=list)


@dataclass
class ConsensusResult:
    content: str
    raw_merged_content: str
    provenance: list[SectionProvenance]
    conflicts: list[ConflictRecord]
    optimization: OptimizationReport
    token_count: int
    polished_by: Optional[str] = None
    polish_result: Optional[LLMResult] = None


# --- Phase 1: extraction ---------------------------------------------------


def canonicalize_heading(title: str) -> str:
    """Map a free-form heading onto the canonical section taxonomy."""
    lowered = re.sub(r"[^a-z0-9 &/-]", " ", title.lower()).strip()
    best_key = "additional"
    best_hit = 0
    for key, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                # Longer alias matches are more specific.
                if len(alias) > best_hit:
                    best_hit = len(alias)
                    best_key = key
    return best_key


def split_units(body: str) -> list[str]:
    """Split a section body into atomic directives (bullets, numbered items, sentences)."""
    units: list[str] = []
    buffer: list[str] = []
    in_code = False

    def flush() -> None:
        if buffer:
            joined = " ".join(part.strip() for part in buffer if part.strip())
            if joined:
                units.append(joined)
            buffer.clear()

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            flush()
            in_code = not in_code
            units.append(line)
            continue
        if in_code:
            units.append(line)
            continue
        if not stripped:
            flush()
            continue
        if re.match(r"^([-*+]|\d+[.)])\s+", stripped):
            flush()
            units.append(stripped)
            continue
        buffer.append(stripped)

    flush()

    expanded: list[str] = []
    for unit in units:
        if unit.startswith(("-", "*", "+", "`")) or unit[:1].isdigit() or len(unit) < 200:
            expanded.append(unit)
            continue
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", unit)
        expanded.extend(sentence.strip() for sentence in sentences if sentence.strip())
    return expanded


def parse_sections(content: str) -> list[tuple[str, str, str]]:
    """Return (canonical_key, original_title, body) triples in document order."""
    lines = content.splitlines()
    sections: list[tuple[str, str, list[str]]] = []
    preamble: list[str] = []
    in_code = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
        match = None if in_code else HEADING_RE.match(line)
        if match:
            title = match.group("title").strip().strip("#").strip()
            sections.append((canonicalize_heading(title), title, []))
        elif sections:
            sections[-1][2].append(line)
        else:
            preamble.append(line)

    parsed: list[tuple[str, str, str]] = []
    leading = "\n".join(preamble).strip()
    if leading:
        parsed.append(("role", "Role & Objective", leading))
    for key, title, body_lines in sections:
        parsed.append((key, title, "\n".join(body_lines).strip()))
    return parsed


def _normalized_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {word for word in words if word not in STOPWORDS and len(word) > 2}


def similarity(left: str, right: str) -> float:
    """Blend lexical overlap with sequence similarity for robust dedupe."""
    left_tokens, right_tokens = _normalized_tokens(left), _normalized_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    ratio = SequenceMatcher(None, left.lower(), right.lower()).ratio()
    return round(0.6 * jaccard + 0.4 * ratio, 4)


def score_section(canonical: str, body: str, verdict: JudgeVerdict, weight: float) -> float:
    """Score a section variant: judge signal for the metrics it governs, plus density."""
    metric_keys = SECTION_METRICS.get(canonical, ("instruction_clarity",))
    values = [verdict.metrics[key] for key in metric_keys if key in verdict.metrics]
    base = sum(values) / len(values) if values else verdict.overall_score

    words = len(body.split())
    if words == 0:
        return 0.0
    # Reward substance, penalise both anaemic and bloated sections.
    if words < 15:
        density = 0.70
    elif words <= 220:
        density = 1.0
    elif words <= 450:
        density = 0.94
    else:
        density = 0.86

    directives = len(re.findall(r"^\s*([-*+]|\d+[.)])\s+", body, re.MULTILINE))
    structure_bonus = min(6.0, directives * 1.2)
    specificity_bonus = 4.0 if ("{" in body or "<" in body or "```" in body) else 0.0

    return round(min(100.0, base * density + structure_bonus + specificity_bonus) * weight, 3)


def extract_variants(candidates: Sequence[CandidateInput]) -> dict[str, list[SectionVariant]]:
    """Phase 1 — build the canonical section → variants index."""
    index: dict[str, list[SectionVariant]] = {}
    for candidate in candidates:
        merged_bodies: dict[str, list[tuple[str, str]]] = {}
        for canonical, title, body in parse_sections(candidate.content):
            if not body.strip():
                continue
            merged_bodies.setdefault(canonical, []).append((title, body))

        for canonical, entries in merged_bodies.items():
            title = entries[0][0]
            body = "\n\n".join(body for _, body in entries).strip()
            variant = SectionVariant(
                model_id=candidate.model_id,
                model_name=candidate.model_name,
                canonical=canonical,
                title=title,
                body=body,
                units=split_units(body),
                score=score_section(canonical, body, candidate.verdict, candidate.weight),
            )
            index.setdefault(canonical, []).append(variant)

    for variants in index.values():
        variants.sort(key=lambda variant: variant.score, reverse=True)
    return index


# --- Phase 2: conflict detection & resolution ------------------------------


def _declared_formats(body: str) -> set[str]:
    lowered = body.lower()
    return {token for token in FORMAT_TOKENS if token in lowered}


@dataclass
class ConflictOutcome:
    """Conflicts found in a pass, plus the directives that lost them."""

    records: list[ConflictRecord] = field(default_factory=list)
    losing_units: set[str] = field(default_factory=set)

    def absorb(self, other: "ConflictOutcome") -> None:
        self.records.extend(other.records)
        self.losing_units |= other.losing_units


def _contradiction(left: str, right: str) -> Optional[tuple[str, float]]:
    """Classify a pair of directives as contradictory, or return None.

    Two directives conflict when they address the same subject *and* either
    their polarity is opposed or they assert different numeric limits.

    Lexical similarity alone cannot separate agreement from contradiction: the
    sharpest conflicts are the most similar strings ("escalate below 0.7" vs
    "escalate below 0.9" differ by one character). Polarity and numeric checks
    therefore run before the duplicate threshold is applied, and only pairs that
    survive both are dismissed as restatements.
    """
    score = similarity(left, right)
    if score < SIMILARITY_CONFLICT:
        return None

    if bool(NEGATION_RE.search(left)) != bool(NEGATION_RE.search(right)):
        return "opposing polarity on the same directive", score

    numbers_left = set(NUMBER_RE.findall(left))
    numbers_right = set(NUMBER_RE.findall(right))
    if numbers_left and numbers_right and numbers_left != numbers_right:
        return "conflicting numeric limits", score

    return None


def _semantic_record(
    section_title: str,
    left: SectionVariant,
    right: SectionVariant,
    left_unit: str,
    right_unit: str,
    reason: str,
    score: float,
) -> tuple[ConflictRecord, str]:
    """Build a semantic conflict record and return it with the losing directive."""
    winner, loser = (left, right) if left.score >= right.score else (right, left)
    loser_unit = right_unit if winner is left else left_unit
    return (
        ConflictRecord(
            section=section_title,
            kind="semantic",
            description=(
                f"{left.model_name} and {right.model_name} disagree "
                f"({reason}, similarity {score:.2f}): "
                f"'{left_unit[:120]}' vs '{right_unit[:120]}'"
            ),
            competing_models=[left.model_id, right.model_id],
            resolution=(
                f"Kept the directive from {winner.model_name} (section score "
                f"{winner.score:.1f}); dropped the competing directive from "
                f"{loser.model_name} to preserve determinism."
            ),
            winner_model_id=winner.model_id,
        ),
        loser_unit,
    )


def detect_conflicts(
    canonical: str, variants: Sequence[SectionVariant]
) -> ConflictOutcome:
    """Find syntactic, structural and semantic disagreements within one section."""
    outcome = ConflictOutcome()
    if len(variants) < 2:
        return outcome

    conflicts = outcome.records
    winner = variants[0]

    # Syntactic: competing output format declarations.
    if canonical == "output":
        declarations = {
            variant.model_id: _declared_formats(variant.body) for variant in variants
        }
        distinct = {
            frozenset(formats) for formats in declarations.values() if formats
        }
        if len(distinct) > 1:
            # A prompt may declare exactly one output contract, so the losing
            # declarations are removed from the body rather than merely noted.
            winning_formats = declarations[winner.model_id]
            dropped = 0
            for variant in variants[1:]:
                competing = declarations[variant.model_id] - winning_formats
                if not competing:
                    continue
                for unit in variant.units:
                    lowered = unit.lower()
                    if any(token in lowered for token in competing):
                        outcome.losing_units.add(unit)
                        dropped += 1

            conflicts.append(
                ConflictRecord(
                    section=CANONICAL_TITLES[canonical],
                    kind="syntactic",
                    description=(
                        "Models declared different output formats: "
                        + "; ".join(
                            f"{model_id}={sorted(formats) or ['unspecified']}"
                            for model_id, formats in declarations.items()
                        )
                    ),
                    competing_models=[variant.model_id for variant in variants],
                    resolution=(
                        f"Adopted the format declared by the highest-scoring section "
                        f"({winner.model_name}, {winner.score:.1f}); dropped "
                        f"{dropped} competing declaration(s) so the prompt carries "
                        "exactly one output contract."
                    ),
                    winner_model_id=winner.model_id,
                )
            )

    # Structural: heading naming divergence.
    titles = {variant.title.strip().lower() for variant in variants}
    if len(titles) > 1:
        conflicts.append(
            ConflictRecord(
                section=CANONICAL_TITLES[canonical],
                kind="structural",
                description=(
                    "Divergent heading labels for the same section: "
                    + ", ".join(sorted(variant.title for variant in variants))
                ),
                competing_models=[variant.model_id for variant in variants],
                resolution=f"Normalised to the canonical heading '{CANONICAL_TITLES[canonical]}'.",
                winner_model_id=winner.model_id,
            )
        )

    # Semantic: contradictory directives on the same subject.
    for index, variant in enumerate(variants):
        for other in variants[index + 1 :]:
            for unit in variant.units:
                for other_unit in other.units:
                    verdict = _contradiction(unit, other_unit)
                    if verdict is None:
                        continue
                    reason, score = verdict
                    record, loser_unit = _semantic_record(
                        CANONICAL_TITLES[canonical],
                        variant,
                        other,
                        unit,
                        other_unit,
                        reason,
                        score,
                    )
                    conflicts.append(record)
                    outcome.losing_units.add(loser_unit)
                    break

    return outcome


def detect_cross_section_conflicts(
    index: dict[str, list[SectionVariant]],
) -> ConflictOutcome:
    """Find contradictions between directives models filed under different sections.

    Models routinely place the same rule in different places — one calls it a
    constraint, another an instruction. Comparing section-by-section alone would
    let those contradictions through into the merged prompt, so this pass
    compares every directive against those of *other* models in *other*
    sections. A cheap lexical prefilter keeps the pairwise scan bounded.
    """
    outcome = ConflictOutcome()
    entries: list[tuple[str, SectionVariant, str, set[str]]] = [
        (canonical, variant, unit, _normalized_tokens(unit))
        for canonical, variants in index.items()
        for variant in variants
        for unit in variant.units
        if len(unit.split()) >= 4
    ]

    seen_pairs: set[tuple[str, str]] = set()
    for position, (canonical, variant, unit, tokens) in enumerate(entries):
        if not tokens:
            continue
        for other_canonical, other, other_unit, other_tokens in entries[position + 1 :]:
            if canonical == other_canonical or variant.model_id == other.model_id:
                continue
            if not other_tokens:
                continue
            overlap = len(tokens & other_tokens) / len(tokens | other_tokens)
            if overlap < 0.3:
                continue

            key = (unit, other_unit)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)

            verdict = _contradiction(unit, other_unit)
            if verdict is None:
                continue
            reason, score = verdict
            record, loser_unit = _semantic_record(
                CANONICAL_TITLES[canonical],
                variant,
                other,
                unit,
                other_unit,
                f"{reason}, filed under different sections",
                score,
            )
            outcome.records.append(record)
            outcome.losing_units.add(loser_unit)

    return outcome


def resolve_conflicts(
    index: dict[str, list[SectionVariant]],
) -> tuple[dict[str, list[ConflictRecord]], set[str]]:
    """Phase 2 — run both detection passes and collect the losing directives."""
    per_section: dict[str, list[ConflictRecord]] = {}
    losers: set[str] = set()

    for canonical, variants in index.items():
        outcome = detect_conflicts(canonical, variants)
        per_section[canonical] = outcome.records
        losers |= outcome.losing_units

    cross = detect_cross_section_conflicts(index)
    for record in cross.records:
        canonical = next(
            (key for key, title in CANONICAL_TITLES.items() if title == record.section),
            "additional",
        )
        per_section.setdefault(canonical, []).append(record)
    losers |= cross.losing_units

    return per_section, losers


# --- Phase 3: merge --------------------------------------------------------


def merge_section(
    canonical: str,
    variants: Sequence[SectionVariant],
    losers: Optional[set[str]] = None,
    conflicts: Optional[Sequence[ConflictRecord]] = None,
) -> Optional[MergedSection]:
    """Adopt the strongest variant then graft in novel directives from the rest."""
    live = [variant for variant in variants if not variant.is_empty]
    if not live:
        return None

    base = live[0]
    if losers is None or conflicts is None:
        outcome = detect_conflicts(canonical, live)
        conflicts = outcome.records
        losers = outcome.losing_units

    kept_units = [unit for unit in base.units if unit not in losers]
    grafted: list[str] = []
    contributors: list[str] = []

    for variant in live[1:]:
        variant_contributed = False
        for unit in variant.units:
            if unit in losers or len(unit.split()) < 4:
                continue
            if any(similarity(unit, existing) >= SIMILARITY_DUPLICATE for existing in kept_units):
                continue
            if any(similarity(unit, existing) >= SIMILARITY_DUPLICATE for existing in grafted):
                continue
            # Only graft material that carries a directive or a concrete artefact.
            if not re.search(r"[a-z]{4}", unit.lower()):
                continue
            grafted.append(unit)
            variant_contributed = True
        if variant_contributed:
            contributors.append(variant.model_id)

    body_units = [*kept_units, *grafted]
    body = _render_units(body_units)

    strategy = "adopted"
    if grafted and contributors:
        strategy = "merged"
    if len(live) > 1 and not grafted:
        strategy = "deduplicated"

    provenance = SectionProvenance(
        section=CANONICAL_TITLES[canonical],
        source_model_id=base.model_id,
        source_model_name=base.model_name,
        score=round(base.score, 2),
        merged_from=contributors,
        strategy=strategy,  # type: ignore[arg-type]
    )
    return MergedSection(
        canonical=canonical,
        title=CANONICAL_TITLES[canonical],
        body=body,
        provenance=provenance,
        conflicts=list(conflicts),
    )


def _render_units(units: Iterable[str]) -> str:
    """Re-render directives, keeping bullets tight and prose in paragraphs."""
    lines: list[str] = []
    previous_bullet = False
    in_code = False

    for unit in units:
        stripped = unit.strip()
        if not stripped:
            continue
        if stripped.startswith("```"):
            in_code = not in_code
            lines.append(stripped)
            previous_bullet = False
            continue
        if in_code:
            lines.append(unit)
            continue

        is_bullet = bool(re.match(r"^([-*+]|\d+[.)])\s+", stripped))
        if is_bullet:
            if lines and not previous_bullet:
                lines.append("")
            lines.append(stripped)
        else:
            if lines:
                lines.append("")
            lines.append(stripped)
        previous_bullet = is_bullet

    return "\n".join(lines).strip()


# --- Phase 4: optimisation -------------------------------------------------


def optimize(content: str) -> tuple[str, OptimizationReport]:
    """Deduplicate, de-fluff and normalise the merged prompt."""
    original_tokens = estimate_tokens(content)

    seen: set[str] = set()
    output_lines: list[str] = []
    removed_duplicates = 0
    collapsed_blanks = 0
    normalized_headings = 0
    blank_streak = 0
    in_code = False

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            in_code = not in_code
            output_lines.append(line)
            blank_streak = 0
            continue
        if in_code:
            output_lines.append(raw_line)
            continue

        if not stripped:
            blank_streak += 1
            if blank_streak > 1:
                collapsed_blanks += 1
                continue
            output_lines.append("")
            continue
        blank_streak = 0

        heading = HEADING_RE.match(line)
        if heading:
            if heading.group("hashes") != "##":
                normalized_headings += 1
            output_lines.append(f"## {heading.group('title').strip()}")
            continue

        cleaned = line
        for pattern in FILLER_PATTERNS:
            cleaned = pattern.sub("", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).rstrip()
        if not cleaned.strip():
            continue

        fingerprint = re.sub(r"[^a-z0-9]+", " ", cleaned.lower()).strip()
        if len(fingerprint) > 25:
            if fingerprint in seen:
                removed_duplicates += 1
                continue
            seen.add(fingerprint)

        output_lines.append(cleaned)

    optimized = "\n".join(output_lines).strip() + "\n"
    optimized_tokens = estimate_tokens(optimized)

    report = OptimizationReport(
        original_tokens=original_tokens,
        optimized_tokens=optimized_tokens,
        tokens_saved=max(0, original_tokens - optimized_tokens),
        compression_ratio=round(
            optimized_tokens / original_tokens if original_tokens else 1.0, 4
        ),
        removed_duplicate_lines=removed_duplicates,
        collapsed_whitespace_blocks=collapsed_blanks,
        normalized_headings=normalized_headings,
    )
    return optimized, report


# --- Polish pass -----------------------------------------------------------

CONSENSUS_SYSTEM_PROMPT = """You are the Consensus Agent in a multi-model prompt engineering pipeline.

You are given a mechanically merged system prompt assembled from the best-scoring
sections of several models, along with the conflicts that were already resolved.

Your job is editorial, not creative:
- Preserve every '## ' section heading exactly as given, in the same order.
- Preserve every distinct instruction, constraint, schema and guardrail.
- Fix grammar, remove residual redundancy, and make transitions read as one voice.
- Do not re-open resolved conflicts and do not introduce new requirements.
- Never emit placeholders such as TODO, TBD or [insert ...].

Output the finished prompt only — no commentary, no code fences around the whole document."""


def _required_headings(content: str) -> list[str]:
    return [
        match.group("title").strip().lower()
        for line in content.splitlines()
        if (match := HEADING_RE.match(line))
    ]


class ConsensusEngine:
    """Public entry point for consensus synthesis."""

    def __init__(self, service: Optional[LLMService] = None) -> None:
        self._llm = service or llm_service

    def synthesize_deterministic(
        self, candidates: Sequence[CandidateInput], analysis: Optional[RequirementAnalysis] = None
    ) -> tuple[str, list[SectionProvenance], list[ConflictRecord]]:
        """Phases 1-3: produce the merged prompt without any model involvement."""
        if not candidates:
            raise ValueError("Consensus requires at least one scored candidate")

        index = extract_variants(candidates)
        conflicts_by_section, losing_units = resolve_conflicts(index)

        ordering = list(CANONICAL_ORDER)
        if analysis and analysis.required_sections:
            preferred = [canonicalize_heading(name) for name in analysis.required_sections]
            ordering = list(dict.fromkeys([*preferred, *CANONICAL_ORDER]))

        merged_sections: list[MergedSection] = []
        for canonical in ordering:
            variants = index.get(canonical)
            if not variants:
                continue
            merged = merge_section(
                canonical,
                variants,
                losing_units,
                conflicts_by_section.get(canonical, []),
            )
            if merged is not None:
                merged_sections.append(merged)

        if not merged_sections:
            # No headings anywhere: fall back to the single best candidate body.
            best = max(candidates, key=lambda candidate: candidate.overall)
            return (
                best.content.strip() + "\n",
                [
                    SectionProvenance(
                        section="Full Prompt",
                        source_model_id=best.model_id,
                        source_model_name=best.model_name,
                        score=round(best.overall, 2),
                        merged_from=[],
                        strategy="adopted",
                    )
                ],
                [],
            )

        document = "\n\n".join(
            f"## {section.title}\n\n{section.body}".rstrip()
            for section in merged_sections
        )
        provenance = [section.provenance for section in merged_sections]
        conflicts = [conflict for section in merged_sections for conflict in section.conflicts]
        return document + "\n", provenance, conflicts

    async def synthesize(
        self,
        candidates: Sequence[CandidateInput],
        analysis: Optional[RequirementAnalysis] = None,
        *,
        polish: bool = True,
    ) -> ConsensusResult:
        merged, provenance, conflicts = self.synthesize_deterministic(candidates, analysis)
        optimized, report = optimize(merged)

        polished_by: Optional[str] = None
        polish_result: Optional[LLMResult] = None

        if polish:
            polished, polished_by, polish_result = await self._polish(
                optimized, conflicts, analysis
            )
            if polished is not None:
                optimized, report = optimize(polished)
                report = report.model_copy(
                    update={
                        "original_tokens": estimate_tokens(merged),
                        "tokens_saved": max(
                            0, estimate_tokens(merged) - report.optimized_tokens
                        ),
                        "compression_ratio": round(
                            report.optimized_tokens / max(1, estimate_tokens(merged)), 4
                        ),
                    }
                )

        return ConsensusResult(
            content=optimized,
            raw_merged_content=merged,
            provenance=provenance,
            conflicts=conflicts,
            optimization=report,
            token_count=estimate_tokens(optimized),
            polished_by=polished_by,
            polish_result=polish_result,
        )

    async def _polish(
        self,
        content: str,
        conflicts: Sequence[ConflictRecord],
        analysis: Optional[RequirementAnalysis],
    ) -> tuple[Optional[str], Optional[str], Optional[LLMResult]]:
        try:
            provider = model_registry.get(settings.consensus_model_id)
        except UnknownProviderError:
            enabled = model_registry.enabled()
            provider = enabled[0] if enabled else None
        if provider is None:
            return None, None, None

        conflict_digest = (
            "\n".join(
                f"- [{conflict.kind}] {conflict.section}: {conflict.resolution}"
                for conflict in conflicts[:15]
            )
            or "- No conflicts were detected."
        )
        target_format = analysis.recommended_output_format if analysis else "markdown"

        user_prompt = (
            "# Already-Resolved Conflicts (do not re-open)\n"
            f"{conflict_digest}\n\n"
            f"# Target Output Format Of The Prompt Being Written\n{target_format}\n\n"
            "# Mechanically Merged Prompt\n"
            f"{content}\n\n"
            "Return the polished prompt, preserving every heading and instruction."
        )

        try:
            result = await self._llm.complete(
                provider,
                system_prompt=CONSENSUS_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                phase="consensus",
                temperature=0.2,
            )
        except LLMError as exc:
            logger.warning(
                "consensus_polish_failed", extra={"model_id": provider.id, "error": str(exc)}
            )
            return None, None, None

        polished = result.content.strip()
        if polished.startswith("```"):
            polished = re.sub(r"^```[a-z]*\n|\n```$", "", polished).strip()

        before = set(_required_headings(content))
        after = set(_required_headings(polished))
        dropped = before - after
        if dropped:
            logger.warning(
                "consensus_polish_rejected_dropped_sections",
                extra={"dropped": sorted(dropped), "model_id": provider.id},
            )
            return None, None, result

        if estimate_tokens(polished) < estimate_tokens(content) * 0.55:
            logger.warning(
                "consensus_polish_rejected_over_compression",
                extra={"model_id": provider.id},
            )
            return None, None, result

        return polished, provider.id, result


consensus_engine = ConsensusEngine()
