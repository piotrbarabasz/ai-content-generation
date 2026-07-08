# 00 Product Context

## Product Definition

AI Content Studio is a modular AI content creation workflow engine. It is designed to coordinate content generation across multiple formats and production stages, rather than acting as a fixed video generator.

The product should support:
- short video
- long-form video
- audio-only
- script-only

## Core Product Idea

The system should let a user define a content brief and then run a configurable workflow that can include or skip modules depending on the target output. A single engine should be able to produce:
- a short social video with scenes, voiceover, captions and export
- a longer narrative video with research, script, QA and voiceover
- an audio-only package with narrator audio and transcript
- a script-only artifact for downstream editing or publishing

## What the Product Is Not

AI Content Studio is not a fixed video generator that always produces one kind of output. It is a workflow system where the user decides:
- content type
- content genre
- duration profile
- target platform
- language
- tone
- enabled modules
- disabled modules
- providers

## User-Configurable Inputs

A user should be able to configure the workflow at the start of a project:
- content type: short video, long-form video, audio-only, script-only
- content genre: news, story, documentary, educational, marketing, commentary, etc.
- duration profile: short, medium, long, custom
- target platform: TikTok, Reels, YouTube Shorts, YouTube long-form, podcast, blog, etc.
- language: source language and output language
- tone: neutral, dramatic, explanatory, conversational, promotional
- enabled modules: modules that should run in the workflow
- disabled modules: modules that should be skipped or handled manually
- providers: LLM, TTS, asset, rendering, export and publishing providers

## Product Principles

1. Modular by design
   - The workflow should be built from composable modules with clear contracts.

2. Provider-agnostic
   - The engine should support different providers without forcing a single vendor.

3. Hybrid automation
   - The workflow should support fully automated runs, partially manual runs and review checkpoints.

4. Output-driven
   - The module set should adapt to the requested output type, not the other way around.

5. Reusable across formats
   - The same core objects should support both short-form and long-form content generation.

## Initial Product Scope

The first version should focus on two main production paths:
- a short video workflow based on a brief or transcript
- a long-form script-and-voiceover workflow based on sources or topic input

This scope is intentionally narrow enough to validate the workflow engine before expanding into broader publishing or marketplace features.
