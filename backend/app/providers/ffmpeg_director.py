"""Deterministic still-image direction, one motion pass and measured derived audio."""
from fractions import Fraction
from pathlib import Path
import re
from app.domain.scene_motion import MotionConfig,resolve_scene_motion,ZOOM_STRENGTH
from app.domain.shorts import ShortStyle,overlays,ratio
from app.providers.ffmpeg_shorts import FFmpegShortsRenderer
from app.runtime.media_process import RenderCanceled
from app.runtime.media_worker import media_worker,file_checksum,check_canceled
from app.tts.assembly import inspect_pcm_wav
from PIL import Image

class FFmpegDirectorRenderer(FFmpegShortsRenderer):
    def identity(self):return {**super().identity(),'adapter':'director-stills-v1','motion':'single-restrained-pass-v1','captions':'verified-sentences-v1'}
    def graph(self,root,b,style,segments,logo):
        filters=[];labels=[];fps=b['fps']
        for i,s in enumerate(b['scenes']):
            with Image.open(root/f'image-{i}.png') as image:width,height=image.size
            base=self.filters({'width':width,'height':height},[{'scene_id':s['id'],'start':[0,1],'end':[1000,1]}],
                ShortStyle(mode=s['mode'],focal_points=((s['id'],s['focal']),)))
            base=base.replace('[0:v]',f'[{i}:v]')
            base=re.sub(r'\[(\w+)\]',lambda m:'['+m.group(1)+str(i)+']',base)
            filters.append(base)
            frames=s['end_frame']-s['start_frame'];config=MotionConfig(s['zoom'],s['pan'])
            if config.static:effect=f'trim=end_frame={frames},setpts=PTS-STARTPTS'
            else:
                motion=resolve_scene_motion(s['id'],config);t=f'min(1,on/{max(1,frames-1)})';ease=f'(({t})*({t})*(3-2*({t})))'
                zt=f'(1-{ease})' if motion.zoom_direction=='zoom_out' else ease
                zoom=f'1+{ZOOM_STRENGTH[config.zoom_intensity]}*{zt}'
                direction=1 if motion.pan_direction in {'right','down'} else -1
                pan=f'{direction*.05}*({ease}-.5)' if s['pan']=='subtle' else '0'
                effect=(f"scale=2160:3840,format=yuv444p,zoompan=z='{zoom}':x='(iw-iw/zoom)*(.5+{pan})':"
                        f"y='(ih-ih/zoom)/2':d=1:s=1080x1920:fps={fps},trim=end_frame={frames},setpts=PTS-STARTPTS")
            filters.append(f'[v{i}]{effect},setsar=1,format=yuv420p[scene{i}]');labels.append(f'[scene{i}]')
        filters.append(''.join(labels)+f"concat=n={len(labels)}:v=1:a=0[whole]")
        current='whole'
        if logo:
            index=len(b['scenes'])+1
            filters.extend([f'[{index}:v]scale=140:140:force_original_aspect_ratio=decrease[logo]',f'[{current}][logo]overlay=W-w-100:100:shortest=1[branded]']);current='branded'
        if style.burn or style.hook:
            (root/'overlays.ass').write_text(overlays(style,segments),encoding='utf-8')
            filters.append(f'[{current}]ass=overlays.ass[out]');current='out'
        return ';'.join(filters),'['+current+']'
    async def render(self,root,b,audio,style,segments,*,logo=False,canceled,progress):
        root=Path(root);fps=Fraction(b['fps']);duration=Fraction(audio.frame_count,audio.sample_rate)
        def prepare(stop):
            check_canceled(stop)
            parameters,_=inspect_pcm_wav((root/'audio.wav').read_bytes())
            if (parameters.frame_count,parameters.sample_rate,parameters.channels,parameters.sample_width)!=(audio.frame_count,audio.sample_rate,1,2) or file_checksum(root/'audio.wav',stop)!=audio.checksum:
                raise ValueError('Staged director narration differs from verified PCM.')
            return self.graph(root,b,style,segments,logo)
        graph,video=await media_worker(prepare,canceled=canceled)
        if not 30<=duration<=60:raise ValueError('Narration duration blocks rendering.')
        command=[self.ffmpeg,'-nostdin','-v','error','-xerror','-n']
        for i,s in enumerate(b['scenes']):command+=['-loop','1','-framerate',str(fps),'-i',f'image-{i}.png']
        command+=['-i','audio.wav']
        if logo:command+=['-loop','1','-framerate',str(fps),'-i','logo.png']
        progress('Encoding directed Short',0,1)
        command+=['-filter_complex_threads','1','-filter_complex',graph,'-map',video,'-map',f"{len(b['scenes'])}:a:0",
            '-c:v','libx264','-preset','veryfast','-crf','20','-threads','2','-pix_fmt','yuv420p','-r',str(fps),
            '-c:a','aac','-ar','48000','-ac','1','-b:a','192k','-movflags','+faststart','-frames:v',str(b['scenes'][-1]['end_frame']),
            '-progress','pipe:1','director.mp4']
        await self.process.run(command,cwd=root,canceled=canceled,
            on_line=lambda line:progress('Encoding directed Short',int(line[6:]),b['scenes'][-1]['end_frame']) if line.startswith('frame=') and line[6:].isdigit() else None)
        progress('Probing and fully decoding directed Short',0,1)
        output=await self.probe(root,'director.mp4',canceled)
        self.validate_portrait(output)
        expected=b['scenes'][-1]['end_frame']/fps
        if ((output['width'],output['height'],output['sample_rate'],output['channels'],output['fps'])!=(1080,1920,48000,1,fps)
            or output['frame_count']!=b['scenes'][-1]['end_frame'] or abs(output['video_duration']-expected)>Fraction(2,1000000)
            or abs(output['audio_duration']-duration)>Fraction(1024,48000)
            or abs(output['video_duration']-output['audio_duration'])>Fraction(1,fps)+Fraction(1024,48000)):
            raise ValueError('Director codecs, frame count, duration or A/V synchronization failed.')
        await self.packet_check(root,'director.mp4',output,canceled)
        await self.process.run([self.ffmpeg,'-nostdin','-v','error','-xerror','-err_detect','explode','-i','director.mp4',
            '-map','0:v:0','-map','0:a:0','-f','null','-'],cwd=root,canceled=canceled)
        if canceled():raise RenderCanceled('Director render canceled after decode.')
        path=root/'director.mp4'
        measured=await media_worker(lambda stop:file_checksum(path,stop),canceled=canceled)
        report={**output,'checksum':measured,'size_bytes':path.stat().st_size,'fully_decoded':True,
            'video_codec':'h264','audio_codec':'aac','packet_timestamps_verified':True,'status':'READY','fps':str(fps)}
        for k in ('video_duration','audio_duration'):report[k]=ratio(output[k])
        return report

    async def preview(self,root,b,style,segments,*,canceled):
        message='Director preview requires exactly one scene rebased to frame 0 with a positive frame span and FPS; use the selected-scene preview. Full boards are supported by render().'
        if not isinstance(b,dict) or not isinstance(b.get('scenes'),list) or len(b['scenes'])!=1:
            raise ValueError(message)
        scene=b['scenes'][0]
        try:
            fps=Fraction(b['fps'])
            valid=(isinstance(scene,dict) and {'id','mode','focal','zoom','pan','start_frame','end_frame'}<=scene.keys()
                and type(scene['start_frame']) is int and scene['start_frame']==0
                and type(scene['end_frame']) is int and scene['end_frame']>0 and fps>0)
        except (KeyError,ValueError,TypeError,ZeroDivisionError):valid=False
        if not valid:raise ValueError(message)
        root=Path(root)
        graph,video=await media_worker(lambda stop:self.graph(root,b,style,segments,False),canceled=canceled)
        await self.process.run([self.ffmpeg,'-nostdin','-v','error','-n','-loop','1','-framerate',b['fps'],'-i','image-0.png',
            '-filter_complex_threads','1','-filter_complex',graph,'-map',video,'-ss',str(float(min(Fraction(1,2),Fraction(scene['end_frame']-1)/fps))),'-frames:v','1','-threads','1','preview.png'],cwd=root,canceled=canceled)
        return (root/'preview.png').read_bytes()
