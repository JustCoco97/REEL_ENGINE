"""Provider-neutral planning for the first AI Video Director workflow.

No AI service is bundled. The built-in planner produces an explicitly labeled,
deterministic editorial draft and a normal Timeline JSON v1 project.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .project import Project, create_project, save_project


FORMATS = {
    "vertical": {"width": 1080, "height": 1920, "fps": 30},
    "horizontal": {"width": 1920, "height": 1080, "fps": 30},
    "square": {"width": 1080, "height": 1080, "fps": 30},
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


@dataclass(frozen=True)
class DirectorBrief:
    topic: str
    duration_seconds: float
    format: str
    instructions: str
    media_paths: tuple[Path, ...]


@dataclass(frozen=True)
class DirectorPlan:
    brief: DirectorBrief
    provider_id: str
    provider_status: str
    phases: dict
    scenes: tuple[dict, ...]
    timeline: dict


class DirectorProvider(Protocol):
    """Provider adapter contract; future AI services implement this boundary."""

    provider_id: str

    def create_plan(self, brief: DirectorBrief, media: tuple[dict, ...]) -> dict: ...


class DirectorError(ValueError):
    """Invalid brief/media or a plan that cannot become a valid project."""


def probe_video_duration(path: Path) -> float:
    """Read source length without decoding frames."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise DirectorError("FFprobe è necessario per controllare la durata dei video selezionati.")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=15,
        )
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise DirectorError(f"Non riesco a leggere la durata del video: {path}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise DirectorError(f"Durata video non valida: {path}")
    return duration


def _interpret(brief: DirectorBrief) -> dict:
    """Deterministic interpretation for the no-model draft provider only."""
    return {
        "summary": brief.topic.strip(),
        "audience": "Da definire",
        "tone": brief.instructions.strip() or "Chiaro e coinvolgente",
    }


def _beats(topic: str, count: int) -> list[tuple[str, str]]:
    candidates = [
        ("Apertura", f"Apri con un'immagine che introduca {topic} e catturi l'attenzione."),
        ("Contesto", f"Mostra il contesto e gli elementi principali di {topic}."),
        ("Sviluppo", f"Sviluppa il punto centrale: {topic}."),
        ("Dettaglio", f"Aggiungi un dettaglio o una prospettiva significativa su {topic}."),
        ("Chiusura", f"Concludi con un'immagine memorabile legata a {topic}."),
    ]
    if count <= len(candidates):
        return candidates[:count]
    return [(f"Scena {index + 1}", f"Sviluppa {topic}: momento {index + 1}.") for index in range(count)]


def _allocate_durations(capacities: list[float | None], target: float) -> list[float]:
    """Equal-share scene lengths, respecting finite video source lengths."""
    if not capacities:
        raise DirectorError("Aggiungi almeno un contenuto multimediale.")
    finite_capacity = sum(value for value in capacities if value is not None)
    if all(value is not None for value in capacities) and finite_capacity + 1e-6 < target:
        raise DirectorError(
            f"I video selezionati durano in totale {finite_capacity:.1f} s, meno dei {target:.1f} s richiesti. "
            "Aggiungi immagini oppure riduci la durata."
        )
    durations = [0.0] * len(capacities)
    active = set(range(len(capacities)))
    remaining = target
    while active:
        share = remaining / len(active)
        capped = [index for index in active
                  if capacities[index] is not None and capacities[index] - durations[index] < share]
        if not capped:
            for index in active:
                durations[index] += share
            break
        for index in capped:
            addition = max(0.0, float(capacities[index]) - durations[index])
            durations[index] += addition
            remaining -= addition
            active.remove(index)
    return durations


class TemplateDraftProvider:
    """Transparent local template engine; it does not claim to be AI."""

    provider_id = "local-template"

    def create_plan(self, brief: DirectorBrief, media: tuple[dict, ...]) -> dict:
        beats = _beats(brief.topic.strip(), len(media))
        return {
            "interpretation": _interpret(brief),
            "narrative": [
                {"title": title, "purpose": description}
                for title, description in beats
            ],
            "script": [
                {"text": f"{description} (testo guida da rivedere)"}
                for index, (_title, description) in enumerate(beats)
            ],
            "storyboard": [
                {"title": title, "description": description,
                 "media_id": media[index]["media_id"]}
                for index, (title, description) in enumerate(beats)
            ],
        }


class _RejectOllamaRedirects(HTTPRedirectHandler):
    def redirect_request(self, _request, _response, code, _message, _headers, _new_url):
        raise DirectorError(f"Redirect HTTP {code} rifiutato: Ollama deve restare sull’endpoint locale.")


_OLLAMA_OPENER = build_opener(ProxyHandler({}), _RejectOllamaRedirects)


def _normalize_json_candidate(raw: str) -> str:
    """Extract one complete JSON object from common model wrappers without repairing syntax."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Risposta vuota: il modello non ha prodotto JSON.")
    text = raw.strip()
    # Thinking-capable Ollama models normally keep this in message.thinking, but tolerate
    # a complete leading block if an older template places it in content.
    if text.startswith("<think>"):
        closing = text.find("</think>")
        if closing < 0:
            raise ValueError("Blocco thinking non terminato prima del JSON.")
        text = text[closing + len("</think>"):].strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("La risposta non contiene un oggetto JSON.")
    depth = 0
    in_string = False
    escaped = False
    end = None
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise ValueError("JSON incompleto o troncato: parentesi dell'oggetto non chiusa.")
    candidate = text[start:end]
    suffix = text[end:].strip()
    if "{" in suffix or "}" in suffix:
        raise ValueError("La risposta contiene più oggetti JSON: risultato ambiguo.")
    return candidate


def _strict_json_loads(raw: str) -> dict:
    candidate = _normalize_json_candidate(raw)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Chiave JSON duplicata: {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"Costante JSON non valida: {value}")

    value = json.loads(candidate, object_pairs_hook=unique_object, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("La radice JSON deve essere un oggetto.")
    return value


def _validate_ollama_payload(value: dict, allowed_media_ids: set[str]) -> dict:
    """Validate and adapt one atomic scene list to the provider-neutral contract."""
    if set(value) != {"interpretation", "scenes"}:
        raise ValueError("Campi radice mancanti o inattesi.")
    interpretation = value["interpretation"]
    if not isinstance(interpretation, dict) or set(interpretation) != {"summary", "audience", "tone"}:
        raise ValueError("Campi interpretazione mancanti o inattesi.")
    for key, text in interpretation.items():
        if not isinstance(text, str) or not text.strip() or len(text) > 5000:
            raise ValueError(f"Interpretazione '{key}' vuota o oltre il limite.")
    scenes = value["scenes"]
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 12:
        raise ValueError("Il modello deve restituire da 1 a 12 scene.")
    required = {"narrative_title", "narrative_purpose", "script", "media_id", "storyboard_title", "storyboard_description"}
    narrative, script, storyboard = [], [], []
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict) or set(scene) != required:
            raise ValueError(f"Scena {index}: campi mancanti o inattesi.")
        for key in required - {"media_id"}:
            text = scene[key]
            limit = 12000 if key == "script" else 5000
            if not isinstance(text, str) or not text.strip() or len(text) > limit:
                raise ValueError(f"Scena {index}: campo '{key}' vuoto o oltre il limite.")
        media_id = scene["media_id"]
        if not isinstance(media_id, str) or media_id not in allowed_media_ids:
            raise ValueError(f"Scena {index}: media_id non presente nel catalogo selezionato.")
        narrative.append({"title": scene["narrative_title"], "purpose": scene["narrative_purpose"]})
        script.append({"text": scene["script"]})
        storyboard.append({"media_id": media_id, "title": scene["storyboard_title"],
                           "description": scene["storyboard_description"]})
    return {"interpretation": interpretation, "narrative": narrative,
            "script": script, "storyboard": storyboard}


class OllamaProvider:
    """Ollama HTTP adapter restricted to loopback, with bounded local diagnostics."""

    _RAW_LIMIT = 65536
    _ERROR_LIMIT = 8192
    _RETENTION = 20

    @staticmethod
    def _safe_diagnostic_text(value: str, limit: int) -> str:
        """Bound and redact likely local paths before persisting server/error text."""
        value = value[:limit]
        patterns = (
            r"(?i)\b[A-Z]:\\(?:[^\s\"'<>|?*]+(?:\\[^\s\"'<>|?*]+)*)?",
            r"\\\\[^\s\"'<>|?*]+",
            r"(?<![\w.])/(?:home|Users|tmp|mnt|private|var/folders)/[^\s\"'<>|]+",
        )
        for pattern in patterns:
            value = re.sub(pattern, "<percorso locale redatto>", value)
        return value

    def __init__(self, model: str, *, base_url: str = "http://127.0.0.1:11434", timeout: float = 120,
                 diagnostic_dir: str | Path | None = None):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise DirectorError("Ollama è consentito solo su HTTP locale (127.0.0.1/localhost).")
        if not model or not model.strip():
            raise DirectorError("Seleziona o inserisci un modello Ollama già installato.")
        if not 1 <= float(timeout) <= 600:
            raise DirectorError("Il timeout Ollama deve essere tra 1 e 600 secondi.")
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout = float(timeout)
        if diagnostic_dir is not None:
            self.diagnostic_dir = Path(diagnostic_dir)
        else:
            local_app_data = os.environ.get("LOCALAPPDATA")
            self.diagnostic_dir = (Path(local_app_data) / "REEL_ENGINE" / "logs" / "ai_director"
                                   if local_app_data else Path(__file__).resolve().parents[2] / "logs" / "ai_director")
        self.provider_id = f"ollama:{self.model}"
        self.provider_status = f"Bozza generata con AI locale Ollama ({self.model}); revisione richiesta."
        self.latest_diagnostic_path: Path | None = None
        self._diagnostic = None
        self._last_http_status = None

    def _begin_diagnostic(self):
        diagnostic_id = uuid.uuid4().hex
        self.latest_diagnostic_path = self.diagnostic_dir / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{diagnostic_id}.json"
        self._diagnostic = {
            "diagnostic_version": 1,
            "id": diagnostic_id,
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "provider": self.provider_id,
            "endpoint": self.base_url,
            "privacy": {
                "request_prompt_saved": False,
                "media_paths_saved": False,
                "thinking_content_saved": False,
                "raw_final_content_limit_bytes": self._RAW_LIMIT,
                "retention_limit_files": self._RETENTION,
            },
            "model_info": None,
            "attempts": [],
            "events": [],
            "outcome": "running",
        }
        self._persist_diagnostic()

    def _persist_diagnostic(self):
        if self._diagnostic is None or self.latest_diagnostic_path is None:
            return
        path = self.latest_diagnostic_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                             prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self._diagnostic, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        files = sorted(path.parent.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for expired in files[self._RETENTION:]:
            try:
                expired.unlink()
            except OSError:
                pass

    def _add_event(self, kind: str, **details):
        if self._diagnostic is None:
            return
        self._diagnostic["events"].append({"at_utc": datetime.now(timezone.utc).isoformat(),
                                           "kind": kind, **details})
        self._persist_diagnostic()

    @staticmethod
    def _response_metadata(response: dict, message: dict) -> dict:
        allowed = ("model", "created_at", "done", "done_reason", "total_duration", "load_duration",
                   "prompt_eval_count", "prompt_eval_cached_count", "prompt_eval_duration", "eval_count", "eval_duration")
        result = {key: response.get(key) for key in allowed if key in response}
        result["message_role"] = message.get("role")
        result["thinking_present"] = bool(message.get("thinking"))
        result["thinking_length"] = len(message.get("thinking") or "") if isinstance(message.get("thinking"), str) else None
        content = message.get("content")
        result["content_length"] = len(content) if isinstance(content, str) else None
        return result

    def _request(self, endpoint: str, payload: dict | None = None, *, timeout: float | None = None) -> dict:
        url = f"{self.base_url}{endpoint}"
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=data, headers={"Accept": "application/json"} if data is None else {
            "Accept": "application/json", "Content-Type": "application/json",
        }, method="GET" if data is None else "POST")
        effective_timeout = self.timeout if timeout is None else timeout
        self._last_http_status = None
        try:
            with _OLLAMA_OPENER.open(request, timeout=effective_timeout) as response:
                status = response.status
                raw = response.read(1_000_001)
            self._last_http_status = status
            if len(raw) > 1_000_000:
                raise DirectorError("La risposta di Ollama supera il limite di 1 MB.")
            try:
                result = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._add_event("http_response_json_error", http_status=status, route=endpoint,
                                body_prefix=raw[:self._ERROR_LIMIT].decode("utf-8", errors="replace"))
                raise DirectorError("Ollama ha restituito un envelope HTTP non valido.") from exc
        except HTTPError as exc:
            body = exc.read(self._ERROR_LIMIT).decode("utf-8", errors="replace")
            self._last_http_status = exc.code
            self._add_event("http_error", http_status=exc.code, route=endpoint,
                            error_body=self._safe_diagnostic_text(body, self._ERROR_LIMIT))
            raise DirectorError(f"Ollama ha risposto HTTP {exc.code}: {body or exc.reason}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError):
                message = f"Timeout Ollama dopo {effective_timeout:g} s. Controlla che il modello sia caricato e riprova."
                self._add_event("http_timeout", route=endpoint, timeout_seconds=effective_timeout)
                raise DirectorError(message) from exc
            self._add_event("http_connection_error", route=endpoint, error_type=type(exc).__name__)
            raise DirectorError("Ollama non raggiungibile su 127.0.0.1:11434. Avvia Ollama e riprova.") from exc
        if not isinstance(result, dict):
            self._add_event("http_envelope_invalid", route=endpoint, http_status=self._last_http_status,
                            response_type=type(result).__name__)
            raise DirectorError("Ollama ha restituito un formato di risposta non valido.")
        return result

    def check_connection(self) -> list[str]:
        """Return installed model names from GET /api/tags; never pulls a model."""
        result = self._request("/api/tags", timeout=min(self.timeout, 5))
        models = result.get("models")
        if not isinstance(models, list):
            raise DirectorError("Risposta Ollama senza elenco modelli valido.")
        names = [item.get("name") for item in models if isinstance(item, dict) and isinstance(item.get("name"), str)]
        if len(names) != len(models):
            raise DirectorError("L'elenco modelli restituito da Ollama è malformato.")
        return names

    def _schema(self) -> dict:
        def string(maximum):
            return {"type": "string", "minLength": 1, "maxLength": maximum}
        scene = {
            "type": "object", "required": ["narrative_title", "narrative_purpose", "script", "media_id",
                                                  "storyboard_title", "storyboard_description"],
            "additionalProperties": False,
            "properties": {
                "narrative_title": string(300), "narrative_purpose": string(1000), "script": string(2000),
                "media_id": string(100), "storyboard_title": string(300), "storyboard_description": string(1000),
            },
        }
        return {
            "type": "object", "required": ["interpretation", "scenes"], "additionalProperties": False,
            "properties": {
                "interpretation": {
                    "type": "object", "required": ["summary", "audience", "tone"], "additionalProperties": False,
                    "properties": {"summary": string(1000), "audience": string(300), "tone": string(300)},
                },
                "scenes": {"type": "array", "minItems": 1, "maxItems": 12, "items": scene},
            },
        }

    def _model_thinking_option(self) -> tuple[object, dict | None]:
        try:
            info = self._request("/api/show", {"model": self.model}, timeout=min(self.timeout, 10))
        except DirectorError as exc:
            # Older Ollama servers may not expose thinking metadata; keep their default.
            if "HTTP 404" in str(exc):
                self._add_event("model_info_unavailable", reason="api_show_not_supported")
                return None, None
            raise
        thinking = info.get("thinking")
        values = thinking.get("values") if isinstance(thinking, dict) else None
        if isinstance(values, list) and False in values:
            return False, {"values": values, "default": thinking.get("default")}
        if isinstance(values, list) and "low" in values:
            return "low", {"values": values, "default": thinking.get("default")}
        return None, thinking if isinstance(thinking, dict) else None

    def _chat_payload(self, brief: DirectorBrief, catalog: list[dict], schema: dict,
                      think_option: object, repair: dict | None = None) -> dict:
        system = (
            "Sei un assistente editoriale. Restituisci SOLO JSON conforme allo schema richiesto. "
            "Interpreta il brief e produci una struttura narrativa, uno script e uno storyboard. "
            "Le scene devono essere 1-12 e ciascuna contiene tutti i campi narrativi, di script, storyboard e media. "
            "Per media_id usa esclusivamente un ID esatto del catalogo disponibile; puoi riutilizzarlo. "
            "Non inventare file o percorsi. Scrivi in italiano. La risposta deve iniziare con { e terminare con }."
        )
        request_data = {
            "topic": brief.topic, "duration_seconds": brief.duration_seconds, "format": brief.format,
            "additional_instructions": brief.instructions, "available_media_catalog": catalog,
        }
        user_content = json.dumps(request_data, ensure_ascii=False)
        messages = [{"role": "system", "content": system}]
        if repair is not None:
            messages.append({"role": "user", "content": (
                "Correggi una volta la risposta precedente. Mantieni l'idea, rispetta tutti i campi e restituisci solo JSON. "
                f"Errore di validazione: {repair['error']}\nRisposta precedente (estratto):\n{repair['raw']}\n"
                f"Brief e catalogo autorizzato:\n{user_content}"
            )})
        else:
            messages.append({"role": "user", "content": user_content})
        payload = {
            "model": self.model, "messages": messages, "stream": False, "format": schema,
            "options": {"temperature": 0, "num_predict": 4096},
        }
        if think_option is not None:
            payload["think"] = think_option
        return payload

    def create_plan(self, brief: DirectorBrief, media: tuple[dict, ...]) -> dict:
        """Ask Ollama for a structured plan, repair once if needed, then validate IDs strictly."""
        self._begin_diagnostic()
        try:
            installed_models = self.check_connection()
            if self.model not in installed_models:
                raise DirectorError(
                    f"Il modello Ollama '{self.model}' non è installato. Scegli un modello nell'elenco "
                    "o installalo con Ollama; REEL ENGINE non scarica modelli."
                )
            think_option, thinking_info = self._model_thinking_option()
            if self._diagnostic is not None:
                self._diagnostic["model_info"] = {"thinking": thinking_info,
                                                  "think_option_requested": think_option}
                self._persist_diagnostic()
            schema = self._schema()
            catalog = [{"media_id": item["media_id"], "filename": Path(item["path"]).name,
                        "type": item["type"], "duration_seconds": item["capacity"]}
                       for item in media]
            allowed_ids = {item["media_id"] for item in catalog}
            repair = None
            for attempt_number in (1, 2):
                route = "/api/chat"
                try:
                    response = self._request(route, self._chat_payload(brief, catalog, schema, think_option, repair))
                except DirectorError:
                    raise
                message = response.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if not isinstance(content, str):
                    error = "Ollama non ha restituito message.content testuale."
                    self._append_attempt(attempt_number, route, response, message if isinstance(message, dict) else {},
                                         parse_status="invalid", parse_error=error, raw_content=None)
                    repair = {"error": error, "raw": "<message.content assente>"}
                else:
                    try:
                        value = _strict_json_loads(content)
                        normalized = _validate_ollama_payload(value, allowed_ids)
                    except (ValueError, TypeError, json.JSONDecodeError) as exc:
                        error = str(exc)
                        self._append_attempt(attempt_number, route, response, message,
                                             parse_status="invalid", parse_error=error, raw_content=content)
                        repair = {"error": error, "raw": content[:self._ERROR_LIMIT]}
                    else:
                        self._append_attempt(attempt_number, route, response, message,
                                             parse_status="valid", parse_error=None, raw_content=content)
                        self._finish_diagnostic("success")
                        return normalized
                if attempt_number == 2:
                    break
                self._add_event("correction_retry", reason=repair["error"][:500])
            raise DirectorError(
                f"Risposta Ollama non valida dopo un tentativo di correzione: {repair['error']}"
            )
        except Exception as exc:
            if self._diagnostic is not None and self._diagnostic.get("outcome") == "running":
                self._add_event("generation_failed", error_type=type(exc).__name__,
                                error=self._safe_diagnostic_text(str(exc), 1000))
                self._finish_diagnostic("failed")
            if self.latest_diagnostic_path:
                raise DirectorError(f"{exc} Diagnostica locale: {self.latest_diagnostic_path}") from exc
            raise

    def _append_attempt(self, number: int, route: str, response: dict, message: dict,
                        *, parse_status: str, parse_error: str | None, raw_content: str | None):
        raw = raw_content or ""
        encoded_size = len(raw.encode("utf-8"))
        bounded_raw = raw.encode("utf-8")[:self._RAW_LIMIT].decode("utf-8", errors="ignore")
        self._diagnostic["attempts"].append({
            "number": number, "route": route, "http_status": self._last_http_status or 200,
            "response_metadata": self._response_metadata(response, message),
            "parse_status": parse_status, "parse_error": parse_error,
            "raw_content": bounded_raw,
            "raw_content_truncated": encoded_size > len(bounded_raw.encode("utf-8")),
            "raw_content_original_bytes": encoded_size,
        })
        self._persist_diagnostic()

    def _finish_diagnostic(self, outcome: str):
        if self._diagnostic is not None:
            self._diagnostic["outcome"] = outcome
            self._diagnostic["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            self._persist_diagnostic()


class DirectorOrchestrator:
    """Run brief interpretation, narrative/script/storyboard, asset mapping and project prep."""

    def __init__(self, provider: DirectorProvider | None = None):
        self.provider = provider or TemplateDraftProvider()

    def plan(self, brief: DirectorBrief) -> DirectorPlan:
        if not isinstance(brief, DirectorBrief):
            raise DirectorError("Brief AI Director non valido.")
        if not isinstance(brief.topic, str) or not brief.topic.strip():
            raise DirectorError("Inserisci l'argomento o la descrizione del video.")
        if (isinstance(brief.duration_seconds, bool) or not isinstance(brief.duration_seconds, (int, float))
                or not math.isfinite(float(brief.duration_seconds)) or not 1 <= brief.duration_seconds <= 600):
            raise DirectorError("La durata deve essere compresa tra 1 e 600 secondi.")
        if brief.format not in FORMATS:
            raise DirectorError("Formato video non riconosciuto.")
        paths = tuple(Path(path).expanduser().resolve(strict=False) for path in brief.media_paths)
        if not paths:
            raise DirectorError("Seleziona almeno un'immagine o un video da associare alle scene.")
        media = []
        for index, path in enumerate(paths, start=1):
            if not path.is_file():
                raise DirectorError(f"File multimediale non trovato: {path}")
            suffix = path.suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                media.append({"media_id": f"media_{index:03d}", "path": path, "type": "image", "capacity": None})
            elif suffix in VIDEO_SUFFIXES:
                media.append({"media_id": f"media_{index:03d}", "path": path, "type": "video", "capacity": probe_video_duration(path)})
            else:
                raise DirectorError(f"Formato media non supportato: {path.name}")

        provider_output = self.provider.create_plan(brief, tuple(media))
        if not isinstance(provider_output, Mapping):
            raise DirectorError("Il provider deve restituire un oggetto strutturato.")
        if set(provider_output) != {"interpretation", "narrative", "script", "storyboard"}:
            raise DirectorError("La risposta provider contiene campi inattesi o mancanti.")
        interpretation = provider_output.get("interpretation")
        if not isinstance(interpretation, Mapping) or set(interpretation) != {"summary", "audience", "tone"}:
            raise DirectorError("Interpretazione del brief incompleta o non valida.")
        if any(not isinstance(interpretation.get(key), str) or not interpretation[key].strip()
               or len(interpretation[key]) > 5000 for key in ("summary", "audience", "tone")):
            raise DirectorError("Interpretazione del brief incompleta o non valida.")
        narrative = provider_output.get("narrative")
        script = provider_output.get("script")
        storyboard = provider_output.get("storyboard")
        if not all(isinstance(value, list) for value in (narrative, script, storyboard)):
            raise DirectorError("Il provider deve restituire liste narrative, script e storyboard.")
        if not (len(narrative) == len(script) == len(storyboard)) or not 1 <= len(storyboard) <= 50:
            raise DirectorError("Il provider deve restituire da 1 a 50 scene con narrativa e script corrispondenti.")
        if any(not isinstance(item, Mapping) for group in (narrative, script, storyboard) for item in group):
            raise DirectorError("Ogni elemento di narrativa, script e storyboard deve essere un oggetto.")
        catalog = {item["media_id"]: item for item in media}
        for index, beat in enumerate(narrative):
            if set(beat) != {"title", "purpose"} or not all(
                    isinstance(beat.get(key), str) and beat[key].strip() and len(beat[key]) <= 5000
                    for key in ("title", "purpose")):
                raise DirectorError(f"Narrativa della scena {index + 1} incompleta o non valida.")
        for index, item in enumerate(script):
            if set(item) != {"text"} or not isinstance(item.get("text"), str) or not item["text"].strip() or len(item["text"]) > 12000:
                raise DirectorError(f"Script della scena {index + 1} vuoto o non valido.")
        matched_media = []
        for index, beat in enumerate(storyboard):
            media_id = beat.get("media_id")
            if not isinstance(media_id, str) or media_id not in catalog:
                raise DirectorError(f"Storyboard scena {index + 1}: media_id non presente nel catalogo selezionato.")
            if set(beat) != {"media_id", "title", "description"} or not all(
                    isinstance(beat.get(key), str) and beat[key].strip() and len(beat[key]) <= 5000
                    for key in ("title", "description")):
                raise DirectorError(f"Storyboard scena {index + 1}: titolo/descrizione mancanti o campi non validi.")
            matched_media.append(catalog[media_id])
        durations = _allocate_durations([item["capacity"] for item in matched_media], float(brief.duration_seconds))
        scenes = []
        for index, (item, duration, beat, story, script_item) in enumerate(
                zip(matched_media, durations, narrative, storyboard, script), start=1):
            scenes.append({
                "id": f"director_scene_{index:03d}",
                "title": str(beat["title"]),
                "description": str(beat["purpose"]),
                "script": str(script_item["text"]),
                "media_id": item["media_id"],
                "asset": str(item["path"]),
                "type": item["type"],
                "duration": duration,
                "source_in": 0.0,
            })
        timeline = {
            "schema_version": 1,
            "canvas": dict(FORMATS[brief.format]),
            "audio": None,
            "scenes": [
                {"id": scene["id"], "type": scene["type"], "asset": scene["asset"],
                 "duration": scene["duration"], "source_in": scene["source_in"]}
                for scene in scenes
            ],
        }
        phases = {
            "interpretation": dict(interpretation),
            "narrative": narrative,
            "script": script,
            "storyboard": storyboard,
            "asset_catalog": [{"media_id": item["media_id"], "filename": item["path"].name,
                               "type": item["type"]} for item in media],
            "asset_matching": [{"scene": scene["id"], "media_id": scene["media_id"]}
                               for scene in scenes],
            "project_preparation": {"timeline_schema_version": 1, "scene_count": len(scenes)},
        }
        provider_status = getattr(
            self.provider, "provider_status",
            "AI provider non configurato; bozza locale deterministica da rivedere.",
        )
        return DirectorPlan(brief, self.provider.provider_id, provider_status, phases, tuple(scenes), timeline)

    @staticmethod
    def create_project(plan: DirectorPlan, name: str, folder: str | Path) -> Project:
        if not isinstance(plan, DirectorPlan):
            raise DirectorError("Genera prima una bozza valida.")
        project = create_project(name, folder, canvas=plan.timeline["canvas"])
        try:
            project.timeline = plan.timeline
            project.settings["ai_director"] = {
                "workflow_version": 1,
                "provider": plan.provider_id,
                "status": plan.provider_status,
                "brief": {
                    "topic": plan.brief.topic,
                    "duration_seconds": plan.brief.duration_seconds,
                    "format": plan.brief.format,
                    "instructions": plan.brief.instructions,
                },
                "phases": plan.phases,
            }
            save_project(project)
        except Exception:
            project.path.unlink(missing_ok=True)
            try:
                project.folder.rmdir()
            except OSError:
                pass
            raise
        return project


__all__ = ["DirectorBrief", "DirectorPlan", "DirectorProvider", "DirectorOrchestrator",
           "DirectorError", "TemplateDraftProvider", "OllamaProvider", "FORMATS"]
