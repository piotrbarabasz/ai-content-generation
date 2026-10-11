"""Resumable orchestration over existing owner-thread project services."""

from dataclasses import dataclass
import inspect
import logging
from app.domain.scene_motion import MotionConfig


logger = logging.getLogger("aics.pipeline")


@dataclass(frozen=True, slots=True)
class AutomaticWorkflowConfig:
    voice_choice: object
    image_generator_id: str | None
    orientation: str
    final_resolution: str
    audio_variant: str = "original"
    zoom_intensity: str = "subtle"
    pan_intensity: str = "off"

    def __post_init__(self):
        MotionConfig(self.zoom_intensity, self.pan_intensity)
        if self.voice_choice is None:
            raise ValueError("Choose a TTS voice before starting the automatic workflow.")
        if self.orientation not in ("landscape", "portrait"):
            raise ValueError("Choose a valid image orientation before starting the automatic workflow.")
        if self.audio_variant not in ("original", "processed"):
            raise ValueError("Choose original or processed automatic timeline audio explicitly.")


@dataclass(frozen=True, slots=True)
class AutomaticWorkflowResult:
    sections: int
    scenes: int
    timeline_clips: int
    duration: str
    skipped: int
    plan_script_groups_completed: int = 0
    plan_script_groups_total: int = 0


class AutomaticWorkflowBlocked(RuntimeError):
    def __init__(self, stage, reason, *, section=None, scene=None):
        super().__init__(reason)
        self.stage, self.reason, self.section, self.scene = stage, reason, section, scene


class AutomaticWorkflowCanceled(RuntimeError):
    pass


class AutomaticWorkflow:
    """Make stage decisions while delegating validation and publication to the driver."""

    def __init__(self, driver):
        self.driver = driver
        self.cancel_requested = False
        self.stage = "SCRIPT"
        self.skipped = 0

    def cancel(self):
        self.cancel_requested = True
        self.driver.cancel_current()

    def _check_cancel(self):
        if self.cancel_requested:
            raise AutomaticWorkflowCanceled(self.stage)

    async def _operation(self, stage, callback, *, section=None, scene=None):
        self.stage = stage
        self._check_cancel()
        try:
            result = callback()
            return await result if inspect.isawaitable(result) else result
        except AutomaticWorkflowCanceled:
            raise
        except (ValueError, RuntimeError, OSError) as exc:
            reason = str(exc) or type(exc).__name__
            logger.error("[AICS][PIPELINE][AUTO][FAIL] stage=%s section=%s scene=%s reason=%r",
                         stage, section or "-", scene or "-", reason)
            raise AutomaticWorkflowBlocked(stage, reason, section=section, scene=scene) from None

    async def run(self, config: AutomaticWorkflowConfig):
        self.cancel_requested = False
        self.skipped = 0
        try:
            configure_motion = getattr(self.driver, "configure_motion", None)
            if callable(configure_motion):
                configure_motion(config)
            if self.driver.unsaved_script_draft():
                logger.error('[AICS][PIPELINE][AUTO][FAIL] stage=SCRIPT reason="Unsaved script draft."')
                raise AutomaticWorkflowBlocked("SCRIPT", "Save or discard the script draft before automatic processing.")
            selected_plan = (self.driver.selected_video_plan()
                             if callable(getattr(self.driver, "selected_video_plan", None)) else None)
            if selected_plan is not None:
                await self._operation("PLAN", lambda: self.driver.validate_plan_run(selected_plan, config))
                logger.info("[AICS][PIPELINE][AUTO][PLAN][OK] format=%s target=%d groups=%d",
                            selected_plan.format.value, selected_plan.target_duration_seconds,
                            len(selected_plan.groups))
                await self._operation("PLAN", lambda: self.driver.ensure_plan_visual_context(selected_plan))
                await self._operation("SCRIPT", lambda: self.driver.ensure_plan_script(selected_plan))
            sections = tuple(self.driver.saved_sections())
            if not sections:
                logger.error('[AICS][PIPELINE][AUTO][FAIL] stage=SCRIPT reason="No saved script."')
                raise AutomaticWorkflowBlocked("SCRIPT", "No saved script.")
            logger.info("[AICS][PIPELINE][AUTO][START] sections=%d", len(sections))
            logger.info("[AICS][PIPELINE][AUTO][CONFIG] image_generator=%s orientation=%s resolution=%s audio_variant=%s",
                        config.image_generator_id or "default", config.orientation,
                        config.final_resolution, config.audio_variant)

            expected_scene_count = 0
            for section in sections:
                self._check_cancel()
                section_label = section.title
                if self.driver.voice_ready(section, config.voice_choice, config.audio_variant):
                    self.skipped += 1
                    logger.info("[AICS][PIPELINE][AUTO][VOICE][SKIP] section=%r", section_label)
                else:
                    logger.info("[AICS][PIPELINE][AUTO][VOICE][BUILD] section=%r provider=%s",
                                section_label, config.voice_choice.provider)
                    await self._operation("VOICE", lambda: self.driver.generate_voice(section, config.voice_choice),
                                          section=section_label)
                    if not self.driver.voice_ready(section, config.voice_choice, config.audio_variant):
                        raise AutomaticWorkflowBlocked("VOICE", "Generated narration did not validate for the current section.",
                                                       section=section_label)
                    logger.info("[AICS][PIPELINE][AUTO][VOICE][OK] section=%r", section_label)

                plan = self.driver.plan_state(section)
                if plan.state == "accepted":
                    logger.info("[AICS][PIPELINE][AUTO][SCENES][SKIP] section=%r acceptance=%s",
                                section_label, plan.acceptance_id)
                    self.skipped += 1
                else:
                    logger.info("[AICS][PIPELINE][AUTO][SCENES][BUILD] section=%r", section_label)
                    try:
                        if plan.state == "none":
                            plan = self.driver.suggest_scene_plan(section)
                        if plan.state != "proposal" or not plan.plan_id:
                            raise ValueError("Scene planning did not produce a current proposal.")
                        plan = self.driver.accept_scene_plan(section, plan.plan_id)
                    except (ValueError, RuntimeError, OSError) as exc:
                        raise AutomaticWorkflowBlocked("SCENES", str(exc) or type(exc).__name__,
                                                       section=section_label) from None
                if plan.state != "accepted":
                    raise AutomaticWorkflowBlocked("SCENES", "A current scene plan could not be accepted.",
                                                   section=section_label)

                if self.driver.timing_ready(section, plan.acceptance_id, config.audio_variant):
                    logger.info("[AICS][PIPELINE][AUTO][TIMING][SKIP] section=%r", section_label)
                    self.skipped += 1
                else:
                    logger.info("[AICS][PIPELINE][AUTO][TIMING][BUILD] section=%r reason=%r",
                                section_label, "Selected narration changed or timing is missing.")
                    try:
                        self.driver.rebuild_timing(section)
                    except (ValueError, RuntimeError, OSError) as exc:
                        raise AutomaticWorkflowBlocked("TIMING", str(exc) or type(exc).__name__,
                                                       section=section_label) from None
                    if not self.driver.timing_ready(section, plan.acceptance_id, config.audio_variant):
                        raise AutomaticWorkflowBlocked("TIMING", "Rebuilt timing does not match selected narration.",
                                                       section=section_label)

                scenes = tuple(self.driver.scenes(section))
                expected_scene_count += len(scenes)
                for scene in scenes:
                    self._check_cancel()
                    if self.driver.prompt_ready(section, scene):
                        logger.info("[AICS][PIPELINE][AUTO][PROMPT][SKIP] scene=%s", scene.id)
                        self.skipped += 1
                    else:
                        logger.info("[AICS][PIPELINE][AUTO][PROMPT][BUILD] scene=%s", scene.id)
                        await self._operation("VISUAL_PROMPT",
                            lambda section=section, scene=scene: self.driver.generate_prompt(section, scene),
                            section=section_label, scene=scene.id)
                        if not self.driver.prompt_ready(section, scene):
                            raise AutomaticWorkflowBlocked("VISUAL_PROMPT", "Generated prompt did not validate for the current scene.",
                                                           section=section_label, scene=scene.id)

                    if self.driver.image_ready(section, scene):
                        provider = self.driver.image_provider_identity(scene) or "unknown"
                        logger.info("[AICS][PIPELINE][AUTO][IMAGE][SKIP] scene=%s provider=%s", scene.id, provider)
                        self.skipped += 1
                    else:
                        logger.info("[AICS][PIPELINE][AUTO][IMAGE][BUILD] scene=%s generator=%s",
                                    scene.id, config.image_generator_id or "default")
                        await self._operation("IMAGE",
                            lambda section=section, scene=scene: self.driver.generate_image(section, scene, config),
                            section=section_label, scene=scene.id)
                        if not self.driver.image_ready(section, scene):
                            raise AutomaticWorkflowBlocked("IMAGE", "Generated image did not validate for the current accepted scene.",
                                                           section=section_label, scene=scene.id)

                    if config.final_resolution == "draft":
                        logger.info("[AICS][PIPELINE][AUTO][FINAL_IMAGE][SKIP] scene=%s resolution=draft", scene.id)
                        self.skipped += 1
                    elif self.driver.motion_master_ready(section, scene, config):
                        logger.info("[AICS][PIPELINE][AUTO][MOTION_MASTER][SKIP] scene=%s resolution=%s",
                                    scene.id, config.final_resolution)
                        self.skipped += 1
                    else:
                        logger.info("[AICS][PIPELINE][AUTO][MOTION_MASTER][BUILD] scene=%s resolution=%s",
                                    scene.id, config.final_resolution)
                        await self._operation("MOTION_MASTER",
                            lambda section=section, scene=scene: self.driver.create_motion_master(section, scene, config),
                            section=section_label, scene=scene.id)
                        if not self.driver.motion_master_ready(section, scene, config):
                            raise AutomaticWorkflowBlocked("MOTION_MASTER", "Motion master did not validate for the requested target.",
                                                           section=section_label, scene=scene.id)

            self._check_cancel()
            try:
                candidates = tuple(self.driver.timeline_candidates(sections, config.audio_variant))
            except (ValueError, RuntimeError, OSError) as exc:
                raise AutomaticWorkflowBlocked("TIMELINE", str(exc) or type(exc).__name__) from None
            rejected = tuple(item for item in candidates if not item.accepted)
            for item in rejected:
                logger.error("[AICS][PIPELINE][TIMELINE][REJECT] section=%r scene=%s variant=%s reason=%r",
                             item.section_title, item.scene_id, item.audio_variant, item.reason)
            if rejected or len(candidates) != expected_scene_count:
                logger.error("[AICS][PIPELINE][AUTO][FAIL] stage=TIMELINE accepted=%d expected=%d",
                             len(candidates) - len(rejected), expected_scene_count)
                raise AutomaticWorkflowBlocked("TIMELINE",
                    f"{len(rejected) or max(0, expected_scene_count - len(candidates))} of {expected_scene_count} scenes rejected.")
            sources = tuple(item.source for item in candidates)
            try:
                timeline_matches = self.driver.timeline_matches(candidates)
            except (ValueError, RuntimeError, OSError) as exc:
                raise AutomaticWorkflowBlocked("TIMELINE", str(exc) or type(exc).__name__) from None
            if timeline_matches:
                self.skipped += 1
                logger.info("[AICS][PIPELINE][AUTO][TIMELINE][SKIP] clips=%d", len(sources))
            else:
                logger.info("[AICS][PIPELINE][AUTO][TIMELINE][BUILD] clips=%d", len(sources))
                try:
                    await self._operation("TIMELINE", lambda: self.driver.rebuild_timeline(sources))
                except AutomaticWorkflowBlocked:
                    raise
            self._check_cancel()
            try:
                current = self.driver.current_timeline()
            except (ValueError, RuntimeError, OSError) as exc:
                raise AutomaticWorkflowBlocked("TIMELINE", str(exc) or type(exc).__name__) from None
            clip_count = len(current.timeline.clips) if current else 0
            duration = str(current.timeline.duration) if current else "0"
            group_counts = (self.driver.plan_script_progress(selected_plan)
                            if selected_plan is not None and callable(getattr(self.driver, "plan_script_progress", None))
                            else (0, 0))
            logger.info("[AICS][PIPELINE][AUTO][DONE] sections=%d scenes=%d timeline_clips=%d duration=%s skipped=%d",
                        len(sections), expected_scene_count, clip_count, duration, self.skipped)
            return AutomaticWorkflowResult(len(sections), expected_scene_count, clip_count, duration, self.skipped,
                                           *group_counts)
        except AutomaticWorkflowCanceled as exc:
            logger.warning("[AICS][PIPELINE][AUTO][CANCEL] stage=%s", exc.args[0] if exc.args else self.stage)
            raise
        except AutomaticWorkflowBlocked:
            raise
        except Exception:
            logger.exception("[AICS][PIPELINE][AUTO][FAIL] stage=%s unexpected_error", self.stage)
            raise
