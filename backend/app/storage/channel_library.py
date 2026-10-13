"""Application-owned immutable channel revisions and assets with transactional heads."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import file_digest
import json
import sqlite3
from uuid import uuid4

from app.domain.channel_library import ChannelProfile, ReusableBrandingConfig, identifier
from app.domain.dependencies import canonical_json
from app.storage.paths import contained_path, database_path, storage_root, import_path
from app.storage.local_store import _publish_file


def copy_bounded(source,target,limit):
    size=0
    with source.open("rb") as incoming,target.open("xb") as output:
        while block:=incoming.read(1024*1024):
            size+=len(block)
            if size>limit: raise ValueError("Asset grew beyond its intake bound while copying.")
            output.write(block)
        output.flush()
        import os
        os.fsync(output.fileno())
    return size


class ChannelLibrary:
    def __init__(self, root):
        self.root = storage_root(root)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version=db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0,1):raise ValueError("Unsupported Channel Library database version; preserve it and upgrade the application.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profiles (revision TEXT PRIMARY KEY, channel TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS heads (channel TEXT PRIMARY KEY, revision TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assets (version TEXT PRIMARY KEY, channel TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(database_path(self.root, "library.sqlite"), timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def profiles(self):
        with self.connection() as db: ids = [r[0] for r in db.execute("SELECT revision FROM heads ORDER BY channel")]
        return tuple(self.profile_revision(r) for r in ids)

    def profile_revision(self, revision):
        identifier(revision)
        with self.connection() as db: row = db.execute("SELECT payload FROM profiles WHERE revision=?", (revision,)).fetchone()
        if row is None: raise ValueError("Shared channel profile revision is missing; restore its channel library.")
        return ChannelProfile.from_payload(json.loads(row[0]))

    def profile(self, channel):
        identifier(channel)
        with self.connection() as db: row = db.execute("SELECT revision FROM heads WHERE channel=?", (channel,)).fetchone()
        if row is None: raise ValueError("Shared channel profile is missing.")
        return self.profile_revision(row[0])

    def save_profile(self, profile, *, expected_revision=None):
        for field, kind in (("logo_version", "logo"), ("intro_version", "intro"), ("outro_version", "outro")):
            version = getattr(profile, field)
            if version:
                asset = self.asset(version)
                if (asset["channel_id"], asset["kind"]) != (profile.channel_id, kind): raise ValueError("Channel asset ownership/type mismatch.")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM heads WHERE channel=?", (profile.channel_id,)).fetchone()
            if (row[0] if row else None) != expected_revision: raise ValueError("Channel profile changed; refresh before saving.")
            db.execute("INSERT INTO profiles VALUES (?,?,?)", (profile.revision_id, profile.channel_id, canonical_json(profile.to_payload())))
            db.execute("INSERT INTO heads VALUES (?,?) ON CONFLICT(channel) DO UPDATE SET revision=excluded.revision", (profile.channel_id, profile.revision_id))
        return profile

    def create(self, name):
        now = datetime.now(timezone.utc).isoformat()
        return self.save_profile(ChannelProfile("channel_"+uuid4().hex, "profile_"+uuid4().hex, name, created_at=now, updated_at=now))

    def edit(self, profile, **changes):
        if set(changes) - {"channel_name", "tagline", "cta", "link", "logo_version", "intro_version", "outro_version", "placement", "intro", "outro", "background", "accent"}:
            raise ValueError("Only public channel metadata/preferences can be edited.")
        updated = replace(profile, revision_id="profile_"+uuid4().hex, updated_at=datetime.now(timezone.utc).isoformat(), **changes)
        return self.save_profile(updated, expected_revision=profile.revision_id)

    def set_default(self, channel):
        self.profile(channel)
        with self.connection() as db: db.execute("INSERT INTO preferences VALUES ('default',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (channel,))

    def default(self):
        with self.connection() as db: row = db.execute("SELECT value FROM preferences WHERE key='default'").fetchone()
        return self.profile(row[0]) if row else None

    def versions(self, channel, kind):
        self.profile(channel)
        with self.connection() as db: rows = db.execute("SELECT payload FROM assets WHERE channel=? AND kind=? ORDER BY rowid", (channel,kind)).fetchall()
        return tuple(json.loads(r[0]) for r in rows)

    def asset(self, version, *, verify=True):
        identifier(version)
        with self.connection() as db: row = db.execute("SELECT payload FROM assets WHERE version=?", (version,)).fetchone()
        if row is None: raise ValueError("Shared asset version is missing; restore/import the associated channel library. No substitution is permitted.")
        value = json.loads(row[0])
        if value["version_id"] != version: raise ValueError("Shared asset identity mismatch.")
        path = contained_path(self.root, value["storage_key"])
        if verify:
            if not path.is_file() or path.stat().st_size != value["size_bytes"]: raise ValueError("Shared asset bytes are missing or changed; restore the exact version.")
            with path.open("rb") as stream:
                if file_digest(stream,"sha256").hexdigest() != value["checksum"]: raise ValueError("Shared asset checksum mismatch.")
        return value

    def path(self, version):
        return contained_path(self.root, self.asset(version)["storage_key"])

    def publish(self, channel, kind, source, media, *, provenance, fingerprint=None):
        self.profile(channel)
        if kind not in {"logo", "intro", "outro", "sound"}: raise ValueError("Unknown channel asset type.")
        if media.get("fully_decoded") is not True:raise ValueError("Publish only validated, fully decoded channel assets.")
        source = import_path(source)
        limit = (20 if kind == "logo" else 32 if kind == "sound" else 256) * 1024 * 1024
        if not 0 < source.stat().st_size <= limit: raise ValueError("Channel asset exceeds its bounded import size.")
        version = "asset_version_"+uuid4().hex
        suffix = ".png" if kind == "logo" else ".audio" if kind == "sound" else ".mp4"
        folder = contained_path(self.root, "assets"); folder.mkdir(exist_ok=True)
        path = contained_path(self.root, "assets/"+version+suffix)
        staging=contained_path(self.root,"assets/pending_"+uuid4().hex+suffix)
        copy_bounded(source,staging,limit)
        with staging.open("rb") as stream: digest=file_digest(stream,"sha256").hexdigest()
        if staging.stat().st_size != source.stat().st_size or (media.get("checksum") and digest != media["checksum"]):
            raise ValueError("Asset changed during import; no version was selected.")
        _publish_file(staging,path)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous=db.execute("SELECT payload FROM assets WHERE channel=? AND kind=? ORDER BY rowid LIMIT 1",(channel,kind)).fetchone()
            asset_id=json.loads(previous[0])["asset_id"] if previous else "channel_asset_"+uuid4().hex
            value={"version":1,"asset_id":asset_id,"version_id":version,"channel_id":channel,"kind":kind,
                "checksum":digest,"size_bytes":path.stat().st_size,"storage_key":path.relative_to(self.root).as_posix(),
                "media":media,"validation":{"fully_decoded":True,"checksum_verified":True,"size_bound_verified":True},"created_at":datetime.now(timezone.utc).isoformat(),"provenance":provenance,"fingerprint":fingerprint}
            db.execute("INSERT INTO assets VALUES (?,?,?,?)",(version,channel,kind,canonical_json(value)))
        return value

    def cached(self, channel, kind, fingerprint):
        for value in reversed(self.versions(channel,kind)):
            if value["fingerprint"] == fingerprint: return self.asset(value["version_id"])
        return None

    def selection(self, profile, **overrides):
        retained=self.profile_revision(profile.revision_id)
        if retained != profile: raise ValueError("Selected profile differs from its immutable revision.")
        def ref(version):
            if not version: return None
            value=self.asset(version)
            if value["channel_id"] != profile.channel_id: raise ValueError("Cross-channel asset selection is forbidden.")
            return {"version_id":version,"checksum":value["checksum"],"duration":value["media"].get("video_duration",[0,1])}
        fields={"intro":profile.intro,"outro":profile.outro,"placement":profile.placement,
            "intro_asset":ref(profile.intro_version),"outro_asset":ref(profile.outro_version),"logo_asset":ref(profile.logo_version)}
        return ReusableBrandingConfig(profile.channel_id,profile.revision_id,profile.channel_name,**(fields|overrides))

    def resolve_selection(self, config):
        profile=self.profile_revision(config.profile_revision)
        if profile.channel_id != config.channel_id or profile.channel_name != config.channel_name: raise ValueError("Pinned profile ownership mismatch.")
        values={}
        for kind, enabled, ref in (("intro",config.intro,config.intro_asset),("outro",config.outro,config.outro_asset)):
            if not enabled:continue
            if ref is None:
                if enabled: raise ValueError("Select an immutable "+kind+" clip version in the Channel Library.")
                continue
            asset=self.asset(ref["version_id"])
            if (asset["channel_id"],asset["kind"],asset["checksum"]) != (config.channel_id,kind,ref["checksum"]) or asset["media"].get("video_duration",[0,1]) != ref["duration"]:
                raise ValueError("Selected asset version/type/checksum/duration mismatch.")
            values[kind]=asset
        return values
