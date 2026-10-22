"""Builds the real provider set for the content pipeline from the environment.

Every key is optional at import time; `build_pipeline_deps` names exactly what is missing so the
worker can log it and keep publishing while the pipeline waits for configuration.
"""

import anthropic

from app.core.config import Settings
from app.providers.claude_json import ClaudeJson
from app.providers.claude_vision import ClaudeVision
from app.providers.elevenlabs_tts import ElevenLabsSpeech
from app.providers.fal_image import FalImageProvider
from app.services.content_pipeline import PipelineDeps, load_prompts


def missing_pipeline_keys(cfg: Settings) -> list[str]:
    names = {"ANTHROPIC_API_KEY": cfg.anthropic_api_key, "FAL_API_KEY": cfg.fal_api_key,
             "ELEVENLABS_API_KEY": cfg.elevenlabs_api_key}
    return [name for name, value in names.items() if not value]


def build_pipeline_deps(cfg: Settings) -> PipelineDeps:
    missing = missing_pipeline_keys(cfg)
    if missing:
        raise RuntimeError("the content pipeline needs: " + ", ".join(missing))
    client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key.get_secret_value())
    return PipelineDeps(
        cfg=cfg,
        llm=ClaudeJson(client=client, model=cfg.claude_model),
        vision=ClaudeVision(client=client, model=cfg.claude_model),
        images=FalImageProvider(api_key=cfg.fal_api_key.get_secret_value(), model=cfg.fal_model),
        speech=ElevenLabsSpeech(api_key=cfg.elevenlabs_api_key.get_secret_value()),
        prompts=load_prompts(cfg.prompts_dir),
    )
