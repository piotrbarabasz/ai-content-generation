import asyncio
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from PIL import Image
import pytest

from app.application.channel_library import ChannelLibraryService
from app.domain.channel_library import ChannelProfile,ReusableBrandingConfig,branding_config
from app.domain.branding import BrandingConfig
from app.storage.channel_library import ChannelLibrary
from app.storage.branding import ProjectBranding
from app.application.projects import ProjectSession
from app.storage.project_repository import ProjectRepository
from app.storage.local_store import LocalArtifactStore


@pytest.fixture
def library(tmp_path):return ChannelLibrary(tmp_path/"shared")


def asset(library,profile,kind,tmp_path,duration=3):
    path=tmp_path/(kind+".bin");path.write_bytes(b"immutable "+kind.encode())
    media={"fully_decoded":True,"checksum":sha256(path.read_bytes()).hexdigest()}
    if kind in {"intro","outro"}:media.update(video_duration=[duration,1],width=640,height=360,fps="25",has_audio=False)
    return library.publish(profile.channel_id,kind,path,media,provenance="deterministic fixture")


def test_profiles_revision_default_and_restart(library):
    p=library.create("Documentary Test Channel");other=library.create("Other channel")
    changed=library.edit(p,tagline="Explore",link="https://example.test/channel")
    library.set_default(other.channel_id)
    reopened=ChannelLibrary(library.root)
    assert reopened.default()==other and reopened.profile(p.channel_id)==changed
    assert reopened.profile_revision(p.revision_id)==p
    assert changed.created_at==p.created_at and changed.updated_at>=p.updated_at
    with pytest.raises(ValueError,match="changed"):library.edit(p,cta="Late edit")
    with pytest.raises(ValueError,match="public"):library.edit(changed,api_key="secret")


def test_two_projects_pin_shared_versions_and_keep_independent_overrides(library,tmp_path):
    profile=library.create("Documentary Test Channel")
    intro=asset(library,profile,"intro",tmp_path);outro=asset(library,profile,"outro",tmp_path,15)
    profile=library.edit(profile,intro_version=intro["version_id"],outro_version=outro["version_id"])
    config=library.selection(profile)
    owners=[]
    for name,value in (("A",config),("B",replace(config,intro=False))):
        session=ProjectSession.create(tmp_path/name,repository_factory=ProjectRepository,name=name)
        branding=ProjectBranding(LocalArtifactStore.for_project(session.repository),session.project.id)
        branding.save(value);owners.append((session,branding))
    try:
        updated=asset(library,profile,"intro",tmp_path,4)
        newer=library.edit(profile,intro_version=updated["version_id"])
        library.set_default(newer.channel_id)
        assert owners[0][1].current()==config and owners[1][1].current().intro is False
        assert library.resolve_selection(config)["intro"]["version_id"]==intro["version_id"]
        assert updated["asset_id"]==intro["asset_id"] and updated["version_id"]!=intro["version_id"]
        assert library.path(intro["version_id"]).read_bytes()==b"immutable intro"
        assert list((tmp_path/"A"/"artifacts").rglob("*.mp4"))==[]
    finally:
        for session,_ in owners:session.close()
    with ProjectSession.open(tmp_path/"A",repository_factory=ProjectRepository) as reopened:
        assert ProjectBranding(LocalArtifactStore.for_project(reopened.repository),reopened.project.id).current()==config


def test_logo_import_retains_original_bytes_alpha_and_checksum(library,tmp_path):
    p=library.create("Logo channel");path=tmp_path/"logo.png"
    Image.new("RGBA",(100,80),(255,255,255,30)).save(path)
    raw=path.read_bytes()
    value=asyncio.run(ChannelLibraryService(library,None).import_asset(p.channel_id,"logo",path))
    path.unlink()
    assert library.path(value["version_id"]).read_bytes()==raw
    assert value["checksum"]==sha256(raw).hexdigest() and value["media"]["fully_decoded"]


@pytest.mark.parametrize("bad",["jpeg","corrupt","dimensions","size"])
def test_logo_intake_rejects_bad_contents_before_selection(library,tmp_path,bad):
    p=library.create("Logo channel");path=tmp_path/"bad.png"
    if bad=="jpeg":Image.new("RGB",(10,10)).save(path,format="JPEG")
    elif bad=="dimensions":Image.new("RGBA",(4097,1)).save(path)
    elif bad=="size":
        with path.open("wb") as stream:stream.truncate(21*1024*1024)
    else:path.write_bytes(b"not an image")
    with pytest.raises((ValueError,OSError)):
        asyncio.run(ChannelLibraryService(library,None).import_asset(p.channel_id,"logo",path))
    assert library.versions(p.channel_id,"logo")==()


def test_missing_corrupted_and_cross_channel_versions_do_not_substitute(library,tmp_path):
    p=library.create("One");other=library.create("Two")
    clip=asset(library,p,"intro",tmp_path)
    with pytest.raises(ValueError,match="ownership"):library.edit(other,intro_version=clip["version_id"])
    with pytest.raises(ValueError,match="missing"):library.asset("missing")
    library.path(clip["version_id"]).write_bytes(b"bad")
    with pytest.raises(ValueError,match="changed"):library.asset(clip["version_id"])


def test_library_rejects_traversal_and_symlink_asset_escape(library,tmp_path):
    with pytest.raises(ValueError):library.asset("../outside")
    p=library.create("Safe");clip=asset(library,p,"intro",tmp_path)
    path=library.path(clip["version_id"]);path.unlink()
    outside=tmp_path/"outside.mp4";outside.write_bytes(b"immutable intro")
    try:path.symlink_to(outside)
    except OSError:pytest.skip("Symlink creation privilege unavailable on this Windows host")
    with pytest.raises(ValueError,match="link"):library.asset(clip["version_id"])


def test_legacy_payload_interpretation_is_preserved():
    legacy=BrandingConfig(channel_name="Legacy",intro=True,placement="before")
    assert branding_config(legacy.to_payload())==legacy
    with pytest.raises(ValueError):ReusableBrandingConfig.from_payload({"version":2,"mode":"reusable_clips"})


def test_asset_publication_checks_media_checksum_and_keeps_previous_version(library,tmp_path):
    p=library.create("Atomic");old=asset(library,p,"intro",tmp_path)
    path=tmp_path/"changed.mp4";path.write_bytes(b"changed bytes")
    with pytest.raises(ValueError,match="changed"):
        library.publish(p.channel_id,"intro",path,{"fully_decoded":True,"checksum":"a"*64},provenance="import")
    assert library.versions(p.channel_id,"intro")== (old,)


def test_disabled_intro_and_baked_logo_do_not_require_unused_bytes(library,tmp_path):
    p=library.create("Independent optional components")
    intro=asset(library,p,"intro",tmp_path);outro=asset(library,p,"outro",tmp_path,15)
    p=library.edit(p,intro_version=intro["version_id"],outro_version=outro["version_id"])
    config=library.selection(p,intro=False)
    library.path(intro["version_id"]).unlink()
    assert library.resolve_selection(config)=={"outro":outro}
    with pytest.raises(ValueError,match="missing"):library.resolve_selection(replace(config,intro=True))
