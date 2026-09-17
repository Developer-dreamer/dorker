import time
from dataclasses import dataclass
from rich.console import Group
from rich.progress import BarColumn, Progress, TextColumn
from rich.text import Text

@dataclass
class ATSState:
    current: int = 0
    total: int = 0
    slug: str = ""
    found: int = 0
    queued: int = 0
    dupes: int = 0

@dataclass
class Counts:
    success: int = 0
    not_found: int = 0
    error: int = 0
    jobs_scraped: int = 0
    jobs_queued: int = 0
    jobs_deduped: int = 0

@dataclass
class DescCounts:
    cache: int = 0
    present: int = 0
    fetched: int = 0
    missing: int = 0
    error: int = 0


class Dashboard:
    def __init__(self, ats_list: list[str]):
        self.pending = ats_list.copy()
        self.working: dict[str, ATSState] = {}
        self.finished: list[str] = []
        self.start_time = time.time()
        self.total_ats = len(ats_list)

    def generate_layout(self) -> Group:
        completed = len(self.finished)
        total = self.total_ats

        progress = Progress(
            BarColumn(bar_width=60, complete_style="green", finished_style="green"),
            TextColumn("{task.completed} / {task.total} ({task.percentage:>3.0f}%)", style="green"),
        )
        progress.add_task("", total=total, completed=completed)

        pending_str = ", ".join(self.pending[:5]) + (" ..." if len(self.pending) > 5 else "")
        pending_text = Text(f"Pending list: [ {pending_str} ]", style="white")

        working_lines = [Text("Working:", style="orange3")]
        for ats, state in self.working.items():
            line = f"- {ats} [{state.current}/{state.total}] | {state.slug} | {state.found} found, {state.queued} queued, {state.dupes} dupes"
            working_lines.append(Text(line, style="orange3"))
        if not self.working:
            working_lines.append(Text("- (none)", style="orange3"))

        finished_str = ", ".join(self.finished[-5:]) + (" ..." if len(self.finished) > 5 else "")
        finished_text = Text(f"Finished: [ {finished_str} ]", style="green")

        elapsed_sec = int(time.time() - self.start_time)
        hours, rem = divmod(elapsed_sec, 3600)
        minutes, seconds = divmod(rem, 60)

        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 or hours > 0:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")

        formatted_time = " ".join(parts)
        elapsed_text = Text(f"Elapsed: {formatted_time}", style="deep_sky_blue1")

        return Group(progress, pending_text, *working_lines, finished_text, elapsed_text)
