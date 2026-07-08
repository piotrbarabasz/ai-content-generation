# 05 MVP Boundary

## MVP Goal

The MVP should validate a modular workflow engine that can support two primary production paths:

- Workflow A: short video
- Workflow B: long-form script + voiceover

## Workflow A: Short Video

Workflow A should support the following path:

brief or transcript → scene planning → voiceover optional → captions optional → video render → export

### Supported Inputs
- brief or transcript
- target platform
- duration profile
- optional tone and language

### Supported Outputs
- scene plan
- optional voiceover
- optional captions
- rendered video
- export bundle

## Workflow B: Long-form Script + Voiceover

Workflow B should support the following path:

sources or topic → research → dossier → outline → script → QA → voiceover → export

### Supported Inputs
- topic or source list
- optional research materials
- target duration
- output language and tone

### Supported Outputs
- research artifacts
- dossier
- outline
- script
- QA report
- voiceover
- export bundle

## MVP Scope Split

### Must have
- shared project and workflow model
- configurable brief input
- scene planning for short video workflows
- research, dossier and outline support for long-form workflows
- script generation support for both paths
- QA checkpoint for long-form script output
- optional voiceover support
- optional captions support
- basic export bundle generation
- module enable/disable logic
- simple provider configuration

### Should have
- reusable module contracts and artifact schemas
- review checkpoints before export
- basic metadata generation
- manual override for module outputs
- support for multiple output formats such as script-only, audio-only and short video

### Could have
- thumbnail generation
- limited asset selection and planning
- more advanced caption styling
- basic publishing integration for one or two platforms
- richer provider abstraction

### Later
- full publishing automation
- multi-tenant collaboration
- advanced analytics
- full billing dashboard
- marketplace asset system

## Explicitly Excluded from MVP

The following are explicitly out of scope for the MVP:
- full publishing automation
- multi-tenant collaboration
- advanced analytics
- full billing dashboard
- marketplace asset system

## MVP Constraints

The MVP should emphasize:
- modularity over polish
- clear module contracts over broad feature coverage
- reliable execution of two primary workflows over broad content variety
- reviewability and manual intervention over complete automation
