"""Serialized job queue for the hidden WebEngine scraper."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScrapeJob:
    mode: str
    url: str = ""
    title: str = ""
    priority: int = 0
    id: int = field(default=0, compare=False)


class ScrapeQueue:
    """FIFO within a priority; duplicates are dropped while queued."""

    def __init__(self):
        self._jobs: list[ScrapeJob] = []
        self._next_id = 1

    def enqueue(self, mode, url="", title="", priority=0):
        key = (str(mode), str(url or ""), str(title or ""))
        for job in self._jobs:
            if (job.mode, job.url, job.title) == key:
                return None
        job = ScrapeJob(mode=key[0], url=key[1], title=key[2],
                        priority=int(priority or 0), id=self._next_id)
        self._next_id += 1
        self._jobs.append(job)
        return job

    def pop_next(self):
        if not self._jobs:
            return None
        index = min(range(len(self._jobs)),
                    key=lambda i: (-self._jobs[i].priority, i))
        return self._jobs.pop(index)

    def remove(self, job):
        try:
            self._jobs.remove(job)
        except ValueError:
            pass

    def clear(self):
        self._jobs.clear()

    def __len__(self):
        return len(self._jobs)

    def __iter__(self):
        return iter(list(self._jobs))
