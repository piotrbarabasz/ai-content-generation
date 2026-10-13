"""Explicit local imports and one-time creation. No AI provider is constructed."""
import asyncio
from hashlib import sha256
from uuid import uuid4

from PIL import Image
from app.domain.branding import BrandingConfig
from app.domain.dependencies import content_fingerprint
from app.storage.paths import contained_path, import_path


class ChannelLibraryService:
    def __init__(self, library, renderer):
        self.library,self.renderer=library,renderer

    async def import_asset(self, channel, kind, path, *, canceled=lambda:False):
        self.library.profile(channel)
        if kind not in {"logo","intro","outro","sound"}:raise ValueError("Unsupported channel asset type.")
        source=import_path(path)
        limit=(20 if kind=="logo" else 32 if kind=="sound" else 256)*1024*1024
        if not source.is_file() or not 0 < source.stat().st_size <= limit:
            raise ValueError("Asset is missing/empty or exceeds its bounded intake size.")
        root=contained_path(self.library.root,"work/"+uuid4().hex);root.mkdir(parents=True)
        target=contained_path(root,"input.png" if kind=="logo" else "input.mp4" if kind in {"intro","outro"} else "input.audio")
        from app.storage.channel_library import copy_bounded
        await asyncio.to_thread(copy_bounded,source,target,limit)
        if kind=="logo":
            def verify_logo():
                with Image.open(target) as image:
                    if image.format!="PNG" or not 1<=image.width<=4096 or not 1<=image.height<=4096 or image.width*image.height>16_000_000:
                        raise ValueError("Logo must be an actual PNG of at most 4096 pixels per side/16 million pixels.")
                    image.load()
                    return {"fully_decoded":True,"width":image.width,"height":image.height,"format":"PNG","checksum":sha256(target.read_bytes()).hexdigest()}
            media=await asyncio.to_thread(verify_logo)
        else:
            if self.renderer is None:raise ValueError("Configure trusted local FFmpeg/FFprobe to import video or sound assets.")
            media=await self.renderer.inspect_asset(target,kind,canceled=canceled)
        if canceled():
            from app.runtime.media_process import RenderCanceled
            raise RenderCanceled("Channel import canceled before publication.")
        return await asyncio.to_thread(self.library.publish,channel,kind,target,media,provenance="imported")

    async def create_clip(self, profile, kind, *, duration=None, sound_version=None, width=1280,height=720,fps=25,canceled=lambda:False):
        if self.renderer is None:raise ValueError("Configure trusted local FFmpeg/FFprobe to create reusable clips.")
        if kind not in {"intro","outro"}: raise ValueError("Choose intro or outro creation.")
        duration=duration if duration is not None else (3 if kind=="intro" else 15)
        if type(duration) is not int or not (2<=duration<=4 if kind=="intro" else 10<=duration<=20): raise ValueError("Use 2-4 seconds for intro or 10-20 seconds for outro.")
        if (width,height) not in {(640,360),(1280,720),(1920,1080)} or fps not in {25,30}: raise ValueError("Choose a supported 16:9 size and 25/30 FPS.")
        if self.library.profile_revision(profile.revision_id)!=profile: raise ValueError("Profile differs from its immutable revision.")
        if not profile.logo_version: raise ValueError("Import/select a shared PNG logo before creating a clip.")
        logo=self.library.asset(profile.logo_version)
        sound=self.library.asset(sound_version) if sound_version else None
        if logo["channel_id"]!=profile.channel_id or logo["kind"]!="logo" or sound and (sound["channel_id"]!=profile.channel_id or sound["kind"]!="sound"):
            raise ValueError("Logo/sound asset belongs to another channel or has the wrong type.")
        card=BrandingConfig(channel_name=profile.channel_name,tagline=profile.tagline,cta=profile.cta,
            background=profile.background,accent=profile.accent)
        identity={"policy":"channel-clip-creator-v1","kind":kind,"settings":card.to_payload(),
            "logo":logo["checksum"],"sound":sound["checksum"] if sound else None,"duration":duration,
            "width":width,"height":height,"fps":fps,"renderer":self.renderer.identity()}
        fingerprint=content_fingerprint(identity)
        if canceled():
            from app.runtime.media_process import RenderCanceled
            raise RenderCanceled("Channel creation canceled before cache reuse.")
        cached=self.library.cached(profile.channel_id,kind,fingerprint)
        if cached is not None: return cached
        root=contained_path(self.library.root,"work/"+uuid4().hex);root.mkdir(parents=True)
        await asyncio.to_thread((root/"logo.png").write_bytes,self.library.path(profile.logo_version).read_bytes())
        media=await self.renderer.create_clip(root,card,kind=kind,duration=duration,width=width,height=height,fps=fps,
            sound=self.library.path(sound_version) if sound else None,canceled=canceled)
        if self.library.asset(profile.logo_version)!=logo or sound and self.library.asset(sound_version)!=sound:
            raise ValueError("Channel creation assets changed during rendering.")
        if canceled():
            from app.runtime.media_process import RenderCanceled
            raise RenderCanceled("Channel creation canceled before publication.")
        return await asyncio.to_thread(self.library.publish,profile.channel_id,kind,root/"clip.mp4",media,
            provenance={"operation":"local_clip_creation","identity":identity,"source_versions":{"logo":profile.logo_version,"sound":sound_version}},fingerprint=fingerprint)
