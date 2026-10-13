"""Verified piecewise frame/sample map for reusable finishing, independent of mutable profiles."""
from fractions import Fraction


def ratio(value):
    value=Fraction(value)
    return [value.numerator,value.denominator]


def clip_allocation(duration,fps):
    fps=Fraction(fps)
    quantum=Fraction(48000,fps).denominator
    frames=max(quantum,round(Fraction(duration)*fps/quantum)*quantum)
    actual=Fraction(frames,fps)
    return frames,int(actual*48000),actual


def make_timing_map(source, assets, boundary, checksum):
    fps=source["fps"]
    intro=assets.get("intro",{}); outro=assets.get("outro",{})
    insert_frame=boundary["video_frame"] if intro and boundary else 0
    insert_sample=boundary["audio_sample"] if intro and boundary else 0
    frames=source["frames"]; samples=round(source["audio_duration"]*48000)
    extra_frames=intro.get("frames",0); extra_samples=intro.get("samples",0)
    intervals=[]
    if insert_frame:
        intervals.append({"source_frames":[0,insert_frame],"output_frames":[0,insert_frame],
            "source_samples":[0,insert_sample],"output_samples":[0,insert_sample]})
    intervals.append({"source_frames":[insert_frame,frames],"output_frames":[insert_frame+extra_frames,frames+extra_frames],
        "source_samples":[insert_sample,samples],"output_samples":[insert_sample+extra_samples,samples+extra_samples]})
    return {"version":1,"output_checksum":checksum,"fps":str(fps),"sample_rate":48000,
        "source_frame_count":frames,"source_sample_count":samples,"insertion_frame":insert_frame,"insertion_sample":insert_sample,
        "intro":intro,"outro":outro,"source_intervals":intervals,
        "output_frame_count":frames+extra_frames+outro.get("frames",0),
        "output_sample_count":samples+extra_samples+outro.get("samples",0),
        "output_duration":ratio(Fraction(frames+extra_frames+outro.get("frames",0),fps))}


def verified_timing_map(manifest, original, timeline):
    value=manifest.metadata.get("finishing",{}).get("timing_map")
    if not value or value.get("version") != 1 or value.get("output_checksum") != manifest.checksum:
        raise ValueError("Timed exports unavailable: reusable branded source lacks its verified output timing map.")
    evidence=manifest.metadata["finishing"]
    source=original.metadata["render"]
    fps=Fraction(value["fps"])
    if (value["source_frame_count"] != source["frame_count"] or value["output_frame_count"] != evidence["frame_count"] or
        value["source_sample_count"] != round(Fraction(*source["audio_duration"])*48000) or fps != Fraction(str(source["fps"])) or fps != Fraction(str(evidence["fps"])) or
        abs(Fraction(*value["output_duration"])-Fraction(*evidence["video_duration"]))>Fraction(1,1000000) or value["sample_rate"] != 48000):
        raise ValueError("Timed exports unavailable: rendered frame/sample evidence does not match the reusable map.")
    if abs(Fraction(value["output_sample_count"],48000)-Fraction(*evidence["audio_duration"]))>Fraction(1,fps)+Fraction(1024,48000):
        raise ValueError("Timing map audio samples differ from the measured output stream.")
    identity=manifest.metadata["identity"]
    if identity["source_id"] != original.artifact_id or identity["source_checksum"] != original.checksum:
        raise ValueError("Timing map source identity mismatch.")
    intro=value["intro"];outro=value["outro"]
    for item in (intro,outro):
        if item and (Fraction(item["frames"],fps) != Fraction(item["samples"],48000) or Fraction(*item["duration"]) != Fraction(item["frames"],fps)):
            raise ValueError("Inserted clip frame/sample duration is inconsistent.")
    settings=identity.get("settings",{})
    for kind,item in (("intro",intro),("outro",outro)):
        ref=settings.get(kind+"_asset")
        if bool(item) != bool(settings.get(kind)):
            raise ValueError("Timing map differs from enabled clip selections.")
        if item:
            frames,samples,_=clip_allocation(Fraction(*ref["duration"]),fps)
            if (item["version_id"],item["checksum"],item["frames"],item["samples"]) != (ref["version_id"],ref["checksum"],frames,samples):
                raise ValueError("Timing map does not reference the selected immutable clip.")
    boundary=identity.get("boundary")
    if boundary and (boundary["timeline_id"] != timeline.id or value["insertion_frame"] != boundary["video_frame"] or value["insertion_sample"] != boundary["audio_sample"]):
        raise ValueError("Timing map cold-open boundary mismatch.")
    if not boundary and (value["insertion_frame"] or value["insertion_sample"]): raise ValueError("Timing map has an unverified insertion boundary.")
    expected=make_timing_map({"fps":fps,"frames":source["frame_count"],"audio_duration":Fraction(*source["audio_duration"])},
        {k:v for k,v in (("intro",intro),("outro",outro)) if v},boundary,manifest.checksum)
    if value != expected: raise ValueError("Timing map intervals do not preserve the complete original.")
    return value
