"""Offline PR1 benchmark against the retained October audit's synthetic fixtures.

Usage: python reports/benchmark_indexed_reads.py AUDIT_DIRECTORY OUTPUT_JSON
Does not build media, stage files, render, or open production projects.
"""
import asyncio
import argparse
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("audit_directory", type=Path)
parser.add_argument("output_json", type=Path)
parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
args = parser.parse_args()
sys.path.insert(0, str(args.source_root / "backend"))
from PySide6 import __version__ as qt_version
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from app.application.preview import PreviewService
from app.application.projects import ProjectSession
from app.jobs.repository import JobRepository
from app.storage.artifact_index import ProjectArtifactIndex
from app.storage.local_store import LocalArtifactStore
from app.storage.manifest import ArtifactManifest
from app.storage.preview import ProjectPreviewMedia
from app.storage.project_repository import ProjectRepository
from app.storage.timeline_edits import ProjectTimelineEdits
from app.storage.video_render import ProjectVideoRender, RenderResultIndex

counts = {}
def count(key, n=1):
    counts[key] = counts.get(key, 0) + n

connect = sqlite3.connect
def traced(*args, **kwargs):
    connection = connect(*args, **kwargs)
    connection.set_trace_callback(lambda _: count("sql_statements"))
    return connection
sqlite3.connect = traced
parse = ArtifactManifest.from_payload
def parsed(cls, payload):
    count("manifest_reconstructions")
    return parse(payload)
ArtifactManifest.from_payload = classmethod(parsed)
manifests = ProjectArtifactIndex.manifests
def catalog(self):
    count("full_catalog_scans")
    return manifests(self)
ProjectArtifactIndex.manifests = catalog

app = QCoreApplication([])
results, fixtures = [], []
def measure(name, operation, repetitions=1):
    # Run synchronous owner-thread validation inside a real Qt event loop.
    loop, beats, outcome, errors = QEventLoop(), [], [], []
    heartbeat = QTimer()
    heartbeat.setInterval(5)
    heartbeat.timeout.connect(lambda: beats.append(time.perf_counter()))
    def run():
        counts.clear()
        start = time.perf_counter()
        try:
            for _ in range(repetitions):
                operation()
        except Exception as exc:
            errors.append(exc)
            loop.quit()
            return
        end = time.perf_counter()
        outcome.append({"scenario": name, "repetitions": repetitions,
            "elapsed_ms": (end - start) * 1000,
            "per_operation_ms": (end - start) * 1000 / repetitions,
            "counts": {k: counts.get(k, 0) for k in
                ("full_catalog_scans", "manifest_reconstructions", "sql_statements")}})
        QTimer.singleShot(15, loop.quit)
    heartbeat.start()
    QTimer.singleShot(15, run)
    loop.exec()
    heartbeat.stop()
    if errors:
        raise errors[0]
    outcome[0]["qt_heartbeat_max_ms"] = max((b-a)*1000 for a,b in zip(beats, beats[1:]))
    results.extend(outcome)
    print(json.dumps(outcome[0]), flush=True)

audit, output = args.audit_directory, args.output_json
for fixture in ("cold12sec_v2", "scaling64"):
    workspace = audit / fixture / "project"
    with ProjectSession.open(workspace, repository_factory=ProjectRepository) as session:
        index = RenderResultIndex(session.repository, JobRepository(session.repository))
        store = LocalArtifactStore(index.root, index=index)
        adapter = ProjectVideoRender(index, store)
        history = ProjectTimelineEdits(session.repository, store)
        timeline = history.current().timeline
        headers = index.manifests()
        fixtures.append({"fixture": fixture, "timeline_id": timeline.id, "clips": len(timeline.clips),
            "duration_seconds": float(timeline.duration), "manifest_count": len(headers),
            "catalog_headers_sha256": sha256(json.dumps([m.to_payload() for m in headers],
                                                       sort_keys=True).encode()).hexdigest()})
        if fixture == "cold12sec_v2":
            media = timeline.clips[0].media.image
            key = next(m.storage_key for m in index.manifests() if m.artifact_id == media.artifact_id)
            measure("exact_id_open", lambda: store.open_artifact_id(media.artifact_id).close(), 20)
            measure("exact_key_open", lambda: store.open_artifact(key).close(), 20)
            measure("two_clip_currentness", lambda: adapter.current(timeline))
            proxy = ProjectPreviewMedia(adapter)
            manifest = json.loads(next(proxy.root.glob("*.json")).read_text())
            fixtures[-1].update(proxy_cache_key=manifest["cache_key"], proxy_sha256=manifest["checksum"])
            class CachedRenderer:
                def identity(self):
                    return manifest["renderer"]
                async def render(self, *args, **kwargs):
                    raise AssertionError("Benchmark must hit retained synthetic proxy")
            service = PreviewService(history.current, proxy, CachedRenderer())
            def hit():
                result = asyncio.run(service.proxy(timeline))
                assert result.cached and result.current
            measure("two_clip_cached_proxy_service", hit)
        else:
            image_id = timeline.clips[0].media.image.artifact_id
            key = next(m.storage_key for m in headers if m.artifact_id == image_id)
            measure("exact_id_open_261", lambda: store.open_artifact_id(image_id).close(), 20)
            measure("exact_key_open_261", lambda: store.open_artifact(key).close(), 20)
            for n in (16, 64):
                subset = replace(timeline, clips=timeline.clips[:n])
                measure(f"{n}_clip_readiness", lambda: adapter.current(subset))
output.write_text(json.dumps({"python": sys.version, "pyside6": qt_version,
    "fixtures": fixtures, "results": results}, indent=2) + "\n")
