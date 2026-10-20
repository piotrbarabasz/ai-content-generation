"""Optional director composition, using installed supervised narration providers."""
from app.application.director_audio import DerivedProductionScope
from app.application.shorts_director import ShortsDirectorService
from app.desktop.deployment import media_executables
from app.desktop.product_composition import compose_installed_audio,compose_installed_chatterbox_audio
from app.providers.ffmpeg_director import FFmpegDirectorRenderer

class DirectorAudioProviders:
    """Dispatch unchanged audio services by explicit choice; all own the same child."""
    def __init__(self,services):
        self.services=services;self.store=services[0].store;self.index=services[0].index
        self._prepared_identities={};self.active=services[0]
    def for_selection(self,selection):
        return next(s for s in self.services if selection['provider'] in s.providers)
    def choices(self,language):return tuple(c for s in self.services for c in s.choices(language))
    def selection(self,choice):return self.for_selection(choice.selection()).selection(choice)
    def _resolved_reference(self,selection):return self.for_selection(selection)._resolved_reference(selection)
    def _prepare_voice(self,selection,words,reference):
        service=self.for_selection(selection);prepared=service._prepare_voice(selection,words,reference)
        from app.domain.dependencies import canonical_json
        service._prepared_identities[canonical_json(selection)]=prepared['effective_identity']
        return prepared
    async def generate(self,section,choice):
        self.active=self.for_selection(choice.selection());return await self.active.generate(section,choice)
    def playback(self,section,variant,choice):return self.for_selection(choice.selection()).playback(section,variant,choice)
    def progress(self):return self.active.progress()
    def cancel(self):self.active.cancel()
    @property
    def canceled(self):return self.active.canceled
    @canceled.setter
    def canceled(self,value):
        for s in self.services:s.canceled=value


def compose_director(shorts,candidate_id,*,audio_factory=None,image_provider=None,language=None,_verified_candidate=None):
    def installed(session):
        chatterbox=compose_installed_chatterbox_audio(session)
        configured=(audio_factory or compose_installed_audio)(session)
        services=[s for s in (chatterbox,configured) if s is not None]
        if len(services)==2 and services[0].providers==services[1].providers:services=services[:1]
        return DirectorAudioProviders(services) if len(services)>1 else services[0] if services else None
    def scope(parent,candidate,lang):return DerivedProductionScope(parent,candidate,installed,lang)
    ffmpeg,ffprobe=media_executables()
    renderer=FFmpegDirectorRenderer(ffmpeg,ffprobe) if ffmpeg and ffprobe else None
    return ShortsDirectorService(shorts,candidate_id,scope,renderer,language=language,image_provider=image_provider,_verified_candidate=_verified_candidate)
