"""Reusable operation reports for user-facing Blender diagnostics.

The report model is deliberately independent of Blender UI code so parser and
operator code can record structured results without deciding how they should
be displayed.  The Blender Text datablock writer is kept here as a small
adapter for the explicit "Open Report" action.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


SEVERITIES = ("ERROR", "WARNING", "INFO")


@dataclass(frozen=True)
class ReportEntry:
    """One structured diagnostic belonging to an operation report."""

    severity: str
    category: str
    message: str
    source: str = ""
    destination: str = ""
    suggested_action: str = ""


@dataclass
class OperationReport:
    """Structured outcome and diagnostics for one user operation."""

    operation: str
    text_name: Optional[str] = None
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    entries: list[ReportEntry] = field(default_factory=list)

    def add(
        self,
        severity: str,
        category: str,
        message: str,
        *,
        source: str = "",
        destination: str = "",
        suggested_action: str = "",
    ) -> ReportEntry:
        """Add a diagnostic and return the immutable entry that was stored."""
        normalized_severity = str(severity).upper()
        if normalized_severity not in SEVERITIES:
            raise ValueError(f"Unsupported report severity: {severity}")

        entry = ReportEntry(
            severity=normalized_severity,
            category=str(category),
            message=str(message),
            source=str(source),
            destination=str(destination),
            suggested_action=str(suggested_action),
        )
        self.entries.append(entry)
        return entry

    def info(self, category: str, message: str, **kwargs) -> ReportEntry:
        return self.add("INFO", category, message, **kwargs)

    def warning(self, category: str, message: str, **kwargs) -> ReportEntry:
        return self.add("WARNING", category, message, **kwargs)

    def error(self, category: str, message: str, **kwargs) -> ReportEntry:
        return self.add("ERROR", category, message, **kwargs)

    def set_outcome(self, attempted: int, succeeded: int, failed: int) -> None:
        """Store the operation-level file outcome used by summaries."""
        self.attempted = max(0, int(attempted))
        self.succeeded = max(0, int(succeeded))
        self.failed = max(0, int(failed))

    def counts(self) -> dict[str, int]:
        """Return diagnostic counts keyed by severity."""
        counts = {severity: 0 for severity in SEVERITIES}
        for entry in self.entries:
            counts[entry.severity] += 1
        return counts

    @property
    def status(self) -> str:
        counts = self.counts()
        if self.failed and self.succeeded:
            return "PARTIAL SUCCESS"
        if self.failed or counts["ERROR"]:
            return "FAILED"
        if counts["WARNING"]:
            return "COMPLETED WITH WARNINGS"
        return "SUCCESS"

    @property
    def has_attention(self) -> bool:
        counts = self.counts()
        return bool(self.failed or counts["ERROR"] or counts["WARNING"])

    def summary(self) -> str:
        counts = self.counts()
        return (
            f"{self.operation}: {self.status}\n"
            f"Files: {self.attempted} attempted, {self.succeeded} succeeded, "
            f"{self.failed} failed\n"
            f"Diagnostics: {counts['ERROR']} errors, "
            f"{counts['WARNING']} warnings, {counts['INFO']} info"
        )

    def popup_summary(self) -> str:
        """Return a compact message suitable for a Blender popup."""
        report_name = self.text_name or "Carnivores operation report"
        return f"{self.summary()}\nFull report: {report_name}"

    def to_text(self) -> str:
        """Render a complete report grouped by source and severity."""
        counts = self.counts()
        lines = [
            "CarnivoresIO Operation Report",
            "=" * 31,
            f"Operation: {self.operation}",
            f"Status: {self.status}",
            f"Files: {self.attempted} attempted, {self.succeeded} succeeded, {self.failed} failed",
            (
                "Diagnostics: "
                f"{counts['ERROR']} errors, "
                f"{counts['WARNING']} warnings, "
                f"{counts['INFO']} info"
            ),
            "",
        ]

        if not self.entries:
            lines.append("No detailed diagnostics were recorded.")
            return "\n".join(lines) + "\n"

        grouped: dict[str, dict[str, list[ReportEntry]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for entry in self.entries:
            source = entry.source or "General operation"
            grouped[source][entry.severity].append(entry)

        for source, severity_groups in grouped.items():
            lines.append(f"[{source}]")
            for severity in SEVERITIES:
                for entry in severity_groups.get(severity, []):
                    lines.append(
                        f"{severity} | {entry.category}: {entry.message}"
                    )
                    if entry.destination:
                        lines.append(f"  Destination: {entry.destination}")
                    if entry.suggested_action:
                        lines.append(
                            f"  Suggested action: {entry.suggested_action}"
                        )
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"


def default_report_text_name(operation: str) -> str:
    """Return the stable Text datablock name for a report category."""
    normalized = operation.lower()
    if "import" in normalized:
        return "Carnivores_Import_Report"
    if "export" in normalized:
        return "Carnivores_Export_Report"
    if "validation" in normalized or "preflight" in normalized:
        return "Carnivores_Validation_Report"
    return "Carnivores_Operation_Report"


def write_report_text(report: OperationReport, text_name: Optional[str] = None) -> str:
    """Write a report to a stable Blender Text datablock and return its name."""
    import bpy

    name = text_name or report.text_name or default_report_text_name(report.operation)
    text = bpy.data.texts.get(name)
    if text is None:
        text = bpy.data.texts.new(name)
    text.clear()
    text.write(report.to_text())
    report.text_name = text.name
    return text.name


def get_report_text(text_name: str):
    """Resolve a report Text datablock without changing the current editor."""
    import bpy

    if not text_name:
        return None
    return bpy.data.texts.get(text_name)
