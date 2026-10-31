"""CPU vertical rendering of a single measured excerpt, with independent decode."""
from fractions import Fraction
from pathlib import Path
import json

from app.domain.shorts import crop_box, overlays, ratio
from app.providers.ffmpeg_delivery import FFmpegDeliveryExporter
from app.providers.ffmpeg_render import checksum
from app.runtime.media_process import RenderCanceled
from app.runtime.media_worker import media_worker,file_checksum


class FFmpegShortsRenderer(FFmpegDeliveryExporter):
    def stream_geometry(self, video):
        tags=video.get('tags',{})
        side_data=video.get('side_data_list',[])
        if not isinstance(tags,dict) or not isinstance(side_data,list) or any(not isinstance(item,dict) for item in side_data):
            raise ValueError('Invalid Shorts rotation/display metadata. Normalize an upright, square-pixel MP4 copy before using Shorts.')
        rotation_tags={key:value for key,value in tags.items() if key.lower() in {'rotate','rotation','display_rotation'}}
        matrices=[item for item in side_data if 'display matrix' in str(item.get('side_data_type','')).lower()
                  or 'displaymatrix' in item or 'rotation' in item]
        if rotation_tags or matrices:
            angles=[item.get('rotation','unknown') for item in matrices]+list(rotation_tags.values())
            raise ValueError(f'Shorts does not support rotation/display-matrix metadata (rotation {angles}). '
                             'Normalize a separate upright MP4 copy with square pixels and no display matrix, then verify/select it. The original is unchanged.')
        try:sar=Fraction(video.get('sample_aspect_ratio','1:1').replace(':','/'))
        except (ValueError,AttributeError,ZeroDivisionError) as exc:
            raise ValueError('Invalid Shorts pixel aspect ratio; use a verified square-pixel MP4.') from exc
        if sar!=1:raise ValueError('Shorts requires square-pixel source media; normalize a separate MP4 copy before using Shorts.')
        return {'sar':video.get('sample_aspect_ratio'), 'dar':video.get('display_aspect_ratio'),
                'rotation_metadata':{'tags':rotation_tags,'matrices':matrices}}

    @staticmethod
    def validate_portrait(output):
        try:
            valid=((output['width'],output['height'])==(1080,1920)
                and Fraction(output['sar'].replace(':','/'))==1
                and Fraction(output['dar'].replace(':','/'))==Fraction(9,16)
                and output['field_order']=='progressive'
                and output['rotation_metadata']=={'tags':{},'matrices':[]})
        except (KeyError,TypeError,AttributeError,ValueError,ZeroDivisionError):valid=False
        if not valid:raise ValueError('Short output must be upright progressive 1080x1920, SAR 1:1, DAR 9:16, with no rotation/display matrix. Previous verified output retained.')

    async def preview_geometry(self, root, canceled):
        # Stream metadata is sufficient here. Counting every frame of a long
        # documentary would turn a still preview into a full-source scan.
        raw=await self.process.run([self.ffprobe,'-v','error','-select_streams','v:0','-show_streams','-of','json','source.mp4'],cwd=root,canceled=canceled)
        try:
            streams=json.loads(raw)['streams']
            if len(streams)!=1:raise ValueError('Expected one video stream.')
            video=streams[0];geometry=self.stream_geometry(video)
            return {'width':video['width'],'height':video['height'],**geometry}
        except (KeyError,TypeError,json.JSONDecodeError) as exc:
            raise ValueError('FFprobe preview geometry report is incomplete or invalid.') from exc

    def identity(self):
        return {"adapter": "shorts-vertical-v1", "ffmpeg_sha256": checksum(self.ffmpeg),
                "ffprobe_sha256": checksum(self.ffprobe), "crf": 20, "preset": "veryfast", "overlay": "ass-safe-v1"}

    def filters(self, evidence, shots, style, *, overlay=False):
        if style.mode == "fit":
            graph = ("[0:v]split[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,boxblur=30:2[blur];[fg]scale=1080:1920:force_original_aspect_ratio=decrease[front];"
                "[blur][front]overlay=(W-w)/2:(H-h)/2,setsar=1[framed]")
        else:
            width, height = evidence["width"], evidence["height"]
            w, h, _, y = crop_box(width, height, .5)
            points = dict(style.focal_points)
            expression = str(crop_box(width, height, .5)[2])
            for shot in reversed(shots):
                x = crop_box(width, height, points.get(shot["scene_id"], .5))[2]
                expression = f"if(gte(t,{float(Fraction(*shot['start']))})*lt(t,{float(Fraction(*shot['end']))}),{x},{expression})"
            graph = f"[0:v]crop={w}:{h}:x='{expression}':y={y},scale=1080:1920,setsar=1[framed]"
        return graph + (";[framed]ass=overlays.ass,format=yuv420p[v]" if overlay else ";[framed]format=yuv420p[v]")

    async def render(self, root, evidence, start, end, shots, style, segments, *, canceled, progress, audio_bounds=None):
        root = Path(root); start, end = Fraction(start), Fraction(end)
        source = await self.probe(root, "source.mp4", canceled)
        if (not evidence.get("fully_decoded") or source["frame_count"] != evidence["frame_count"]
                or source["fps"] != Fraction(str(evidence["fps"]))
                or (source["width"], source["height"]) != (evidence["width"], evidence["height"])
                or abs(source["video_duration"] - Fraction(*evidence["video_duration"])) > Fraction(2, 1000000)
                or abs(source["audio_duration"] - Fraction(*evidence["audio_duration"])) > Fraction(2, 1000000)
                or not 0 <= start < end <= min(source["video_duration"], source["audio_duration"]) + Fraction(1, source["fps"])
                or not 30 <= end - start <= 60 or start * source["fps"] % 1 or end * source["fps"] % 1):
            raise ValueError("Short source evidence, frame boundaries or duration is invalid.")
        duration = end - start
        speech_start, speech_end = audio_bounds or (start, end)
        if not start <= speech_start < speech_end <= end or max(speech_start-start, end-speech_end) > Fraction(1, source["fps"]):
            raise ValueError("Audio must use verified speech bounds with at most one frame of silent padding.")
        first_sample, last_sample = round((speech_start-start)*48000), round((speech_end-start)*48000)
        audio_filter = (f"aresample=48000,atrim=start_sample={first_sample}:end_sample={last_sample},asetpts=PTS-STARTPTS,"
                        f"adelay={first_sample}S:all=1,apad=whole_len={round(duration*48000)},atrim=end_sample={round(duration*48000)}")
        (root / "overlays.ass").write_text(overlays(style, segments), encoding="utf-8")
        graph = self.filters(evidence, shots, style, overlay=bool(style.hook or style.burn))
        progress("Encoding vertical excerpt", 0, 1)
        await self.process.run([self.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-n", "-ss", str(float(start)),
            "-i", "source.mp4", "-t", str(float(duration)), "-filter_complex_threads", "1", "-filter_complex", graph,
            "-map", "[v]", "-map", "0:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(source["fps"]), "-threads", "2", "-af", audio_filter,
            "-c:a", "aac", "-ar", "48000", "-b:a", "192k", "-movflags", "+faststart", "short.mp4"], cwd=root, canceled=canceled)
        progress("Probing and decoding Short", 0, 1)
        output = await self.probe(root, "short.mp4", canceled)
        self.validate_portrait(output)
        if ((output["width"], output["height"], output["sample_rate"], output["fps"]) != (1080, 1920, 48000, source["fps"])
                or output["frame_count"] != duration * source["fps"] or not 30 <= output["video_duration"] <= 60
                or abs(output["video_duration"] - duration) > Fraction(2, 1000000)
                or abs(output["audio_duration"] - duration) > Fraction(1024, 48000)
                or abs(output["audio_duration"] - output["video_duration"]) > Fraction(1, source["fps"]) + Fraction(1024, 48000)):
            raise ValueError("Short frame count, duration, dimensions or A/V synchronization failed.")
        await self.packet_check(root, "short.mp4", output, canceled)
        await self.process.run([self.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode", "-i", "short.mp4",
            "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], cwd=root, canceled=canceled)
        if canceled(): raise RenderCanceled("Short canceled after decode.")
        path = root / "short.mp4"
        measured = await media_worker(lambda stop:file_checksum(path,stop),canceled=canceled)
        result = {**output, "fully_decoded": True, "packet_timestamps_verified": True, "checksum": measured,
            "size_bytes": path.stat().st_size, "source_start": ratio(start), "source_end": ratio(end),
            "video_codec": "h264", "audio_codec": "aac", "status": "READY",
            "speech_start": ratio(speech_start), "speech_end": ratio(speech_end)}
        for k in ("video_duration", "audio_duration"): result[k] = ratio(result[k])
        result["fps"] = str(result["fps"])
        progress("Verified Short", 1, 1)
        return result

    async def preview(self, root, evidence, time, shots, style, segments, *, canceled, relative=0):
        root = Path(root)
        source=await self.preview_geometry(root,canceled)
        if (source['width'],source['height'])!=(evidence['width'],evidence['height']):
            raise ValueError('Preview source geometry differs from verified source evidence; refresh the source.')
        (root / "overlays.ass").write_text(overlays(style, segments), encoding="utf-8")
        graph = self.filters(evidence, shots, style, overlay=bool(style.hook or style.burn))
        graph = f"[0:v]setpts=PTS+{float(relative)}/TB[shifted];" + graph.replace("[0:v]", "[shifted]")
        await self.process.run([self.ffmpeg, "-nostdin", "-v", "error", "-n", "-ss", str(float(time)), "-i", "source.mp4",
            "-filter_complex_threads", "1", "-filter_complex", graph,
            "-map", "[v]", "-frames:v", "1", "-threads", "1", "preview.png"], cwd=root, canceled=canceled)
        return (root / "preview.png").read_bytes()
