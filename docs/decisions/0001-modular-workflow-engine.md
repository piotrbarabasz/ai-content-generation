# ADR 0001: Modular Workflow Engine for AI Content Studio

## Status

Accepted

## Context

Istniejące repozytoria pokazują, że wartość nie leży w jednym sztywnym przepływie, ale w zestawie etapów produkcyjnych, które można różnie łączyć. Repo shortów dobrze obsługuje planowanie scen, timing mowy, napisy i render video. Repo long-form dobrze obsługuje research, dossier, outline, script generation, QA i voiceover.

Jeżeli zbudujemy dwa osobne produkty, shorty i long-form, to duplikujemy logikę wejścia, konfiguracji, orchestracji, providerów i artefaktów. Z drugiej strony, jeżeli zbudujemy jeden silnik workflow, możliwe jest wspólne zarządzanie projektami, modułami, artefaktami i konfiguracją.

## Decision

Zdecydowano się zbudować modułowy silnik workflow dla AI Content Studio zamiast dwóch osobnych aplikacji: jednej dla shortów i jednej dla long-form.

Silnik będzie oparty na:
- wspólnym modelu projektu i workflow
- kontraktach modułów
- możliwym włączaniu i wyłączaniu modułów
- wspólnym modelu artefaktów i eksportu
- konfiguracji providerów i presetów workflow

## Consequences

### Positive
- Jedna architektura obsłuży zarówno short video, jak i long-form content.
- Moduły można ponownie wykorzystywać między workflowami.
- Łatwiej dodać nowe typy outputu, takie jak audio-only lub script-only.
- Wspólna warstwa projektów, providerów i artefaktów ogranicza duplikację.
- Możliwe jest stopniowe rozwijanie systemu od MVP do pełniejszej platformy.

### Negative
- Architektura będzie wymagała bardziej ustrukturyzowanego modelu niż dwa osobne, wąsko skrojone produkty.
- Konieczne będzie dobre rozdzielenie odpowiedzialności między moduły, aby uniknąć zbyt silnego powiązania etapów.
- MVP musi być dobrze ograniczone, bo ogólny silnik może łatwo rosnąć poza zakres.

## Alternatives Considered

### 1. Dwa osobne produkty
- Pro: prostsze początkowe projektowanie dla każdej ścieżki.
- Contra: duplikacja logiki, trudniejsza przyszła rozbudowa i słabsza współdzielona infrastruktura.

### 2. Jeden monolityczny pipeline
- Pro: szybkie wdrożenie dla jednego typu outputu.
- Contra: słaba elastyczność, trudne włączanie/wyłączanie etapów i słabe wsparcie dla różnych formatów.

### 3. Modułowy workflow engine
- Pro: najlepsze dopasowanie do obu repozytoriów, elastyczność i przyszła rozszerzalność.
- Contra: wymaga większej dyscypliny projektowej i lepiej zdefiniowanych kontraktów.

## Why This Supports Future Extensibility

Modułowy workflow engine dobrze wspiera przyszłe rozszerzenia, ponieważ:
- nowe typy contentu można dodać przez nowe preset lub nowe połączenia modułów
- nowe providery można podłączyć bez zmiany całego produktu
- nowe etapy, takie jak thumbnail, publishing czy analiza jakości, można dodać jako kolejne moduły
- wspólne artefakty i metadane ułatwiają replay, debugowanie i audit workflow

To podejście jest zgodne z obecnym stanem repozytoriów: nie ma jednego sztywnego przepływu, ale jest zbiór komponentów, które można uporządkować w jednolitą platformę.
