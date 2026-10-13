"""Real local CPU encoding of rewritten narration and an independently timed storyboard."""
import asyncio
from fractions import Fraction as F
from hashlib import sha256
import json
import shutil
from app.providers.ffmpeg_director import FFmpegDirectorRenderer
from app.domain.shorts import ShortStyle
from tests.integration.test_shorts_director import directed,excerpt_fixture,voiced


def test_real_directed_portrait_render_caption_preview_and_parent_preservation(directed):
    s=directed;s.renderer=FFmpegDirectorRenderer(shutil.which('ffmpeg'),shutil.which('ffprobe'))
    original=s.shorts.store.read_artifact(s.fixture.shorts.fixture_source.storage_key)
    voiced(s);asyncio.run(s.generate_storyboard())
    m,b=s.storyboard();b['scenes'][0].update(mode='fill',focal=.25);b['scenes'][1].update(pan='subtle')
    s.save_board(b,m.artifact_id);s.save_finish({'style':ShortStyle(captions=True,burn=True,font_size=32,hook='Money offline',hook_seconds=2).to_payload(),'branding':False})
    preview=asyncio.run(s.preview_scene(b['scenes'][0]['id']))
    from PIL import Image
    from io import BytesIO
    assert Image.open(BytesIO(preview)).size==(1080,1920)
    video=asyncio.run(s.render());e=video.metadata['director']
    assert (e['width'],e['height'],e['fps'],e['frame_count'],e['sample_rate'])==(1080,1920,'25',900,48000)
    assert e['fully_decoded'] and e['packet_timestamps_verified'] and F(*e['video_duration'])==36
    assert abs(F(*e['audio_duration'])-36)<=F(1024,48000)
    assert asyncio.run(s.render()).artifact_id==video.artifact_id
    assert s.shorts.store.read_artifact(s.fixture.shorts.fixture_source.storage_key)==original
    selected=s.records.read(s.records.latest('selection'));meta=s.records.read(s.records.one(selected['metadata_id'],'metadata'))
    assert meta['identity']['audio_checksum']==s.records.read(s.narration_record())['checksum']
    assert meta['identity']['source']['source_id']==s.fixture.source['source_id']
