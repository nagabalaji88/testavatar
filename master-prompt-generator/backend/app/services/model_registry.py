"""Runtime-editable provider registry backed by a JSON specification file."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.models.schemas import ProviderConfig, ProviderRegistryConfig

logger = get_logger(__name__)

DEFAULT_REGISTRY = ProviderRegistryConfig(
    version="1.0",
    providers=[
        ProviderConfig(
            id="openai-gpt4o",
            name="GPT-4o",
            provider="OpenAI",
            model_key="gpt-4o",
            max_tokens=4096,
            cost_per_1k_input=0.0025,
            cost_per_1k_output=0.0100,
            enabled=True,
        ),
        ProviderConfig(
            id="anthropic-claude-sonnet",
            name="Claude 3.5 Sonnet",
            provider="Anthropic",
            model_key="claude-3-5-sonnet-20240620",
            max_tokens=8192,
            cost_per_1k_input=0.0030,
            cost_per_1k_output=0.0150,
            enabled=True,
            supports_json_mode=False,
        ),
        ProviderConfig(
            id="google-gemini-pro",
            name="Gemini 1.5 Pro",
            provider="Google",
            model_key="gemini/gemini-1.5-pro",
            max_tokens=8192,
            cost_per_1k_input=0.00125,
            cost_per_1k_output=0.0050,
            enabled=True,
        ),
    ],
)


class UnknownProviderError(KeyError):
    """Raised when a caller references a provider id that is not registered."""


class ModelRegistry:
    """Loads, caches and mutates the provider catalogue.

    The file is the source of truth so operators can add models by editing a
    mounted JSON document; writes are atomic and guarded by a lock because the
    API and Celery workers may both mutate the registry.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path or settings.model_config_path)
        self._lock = threading.RLock()
        self._registry: Optional[ProviderRegistryConfig] = None
        self._stamp: Optional[tuple[int, int]] = None

    # -- loading -----------------------------------------------------------

    def _file_stamp(self) -> Optional[tuple[int, int]]:
        """(mtime_ns, size) of the spec file, or None when it is absent."""
        try:
            stat = self._path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def load(self, *, force: bool = False) -> ProviderRegistryConfig:
        with self._lock:
            stamp = self._file_stamp()
            # The API and the Celery worker are separate processes sharing this
            # file. Caching on first read alone meant a model added through the
            # API was offered by the UI and then rejected by the worker, whose
            # own cache predated it -- UnknownProviderError on a model the user
            # had just been shown. Re-reading when the file changes is what
            # makes an edit take effect everywhere without a restart.
            if self._registry is not None and not force and stamp == self._stamp:
                return self._registry
            self._stamp = stamp

            if not self._path.exists():
                logger.warning(
                    "model_config_missing_using_defaults",
                    extra={"path": str(self._path)},
                )
                self._registry = DEFAULT_REGISTRY.model_copy(deep=True)
                return self._registry

            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._registry = ProviderRegistryConfig.model_validate(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.error(
                    "model_config_invalid_using_defaults",
                    extra={"path": str(self._path), "error": str(exc)},
                )
                self._registry = DEFAULT_REGISTRY.model_copy(deep=True)
            return self._registry

    def reload(self) -> ProviderRegistryConfig:
        return self.load(force=True)

    # -- queries -----------------------------------------------------------

    def all(self) -> list[ProviderConfig]:
        return list(self.load().providers)

    def enabled(self) -> list[ProviderConfig]:
        return [provider for provider in self.load().providers if provider.enabled]

    def get(self, provider_id: str) -> ProviderConfig:
        for provider in self.load().providers:
            if provider.id == provider_id:
                return provider
        raise UnknownProviderError(provider_id)

    def resolve(self, provider_ids: Optional[list[str]]) -> list[ProviderConfig]:
        """Resolve a requested selection, defaulting to every credentialed provider.

        Enabled-but-uncredentialed entries are dropped from the *default* fan-out
        rather than dispatched: every one of them costs the full retry ladder
        before failing, and a run whose models all lack keys dies several
        seconds in with a wall of provider exceptions instead of saying which
        credential is missing. An explicit selection is still honoured as
        given, so an operator can always force a model and see its real error.
        """
        from app.services.llm_service import requires_credential

        if not provider_ids:
            enabled = self.enabled()
            if not enabled:
                raise UnknownProviderError("no enabled providers are configured")
            selected = [p for p in enabled if not requires_credential(p)]
            if not selected:
                raise UnknownProviderError(
                    "every enabled provider is missing its credential: "
                    + ", ".join(
                        f"{p.id} (set {p.api_key_env})"
                        if p.api_key_env
                        else f"{p.id} ({p.provider})"
                        for p in enabled
                    )
                )
            if skipped := [p.id for p in enabled if requires_credential(p)]:
                logger.warning(
                    "providers_skipped_missing_credential",
                    extra={"skipped": skipped},
                )
            return selected

        resolved: list[ProviderConfig] = []
        missing: list[str] = []
        for provider_id in provider_ids:
            try:
                resolved.append(self.get(provider_id))
            except UnknownProviderError:
                missing.append(provider_id)
        if missing:
            raise UnknownProviderError(", ".join(missing))
        return resolved

    # -- mutation ----------------------------------------------------------

    def upsert(self, provider: ProviderConfig) -> ProviderConfig:
        with self._lock:
            registry = self.load()
            providers = [p for p in registry.providers if p.id != provider.id]
            providers.append(provider)
            self._persist(
                ProviderRegistryConfig(version=registry.version, providers=providers)
            )
            return provider

    def set_enabled(self, provider_id: str, enabled: bool) -> ProviderConfig:
        with self._lock:
            provider = self.get(provider_id).model_copy(update={"enabled": enabled})
            return self.upsert(provider)

    def import_providers(
        self, incoming: list[ProviderConfig], *, replace: bool
    ) -> tuple[list[str], list[str]]:
        """Apply a supplied model list wholesale. Returns (added, updated) ids.

        `replace` swaps the catalogue for exactly what was supplied; otherwise
        the incoming entries are merged over the existing ones by id. Merge is
        the default at the API because replace silently drops working models
        that the uploaded file happens not to mention.

        The whole batch is written in one _persist, so a file that fails
        validation leaves the registry exactly as it was rather than half
        applied.
        """
        with self._lock:
            registry = self.load()
            existing = {p.id: p for p in registry.providers}

            added = [p.id for p in incoming if p.id not in existing]
            updated = [p.id for p in incoming if p.id in existing]

            if replace:
                providers = list(incoming)
            else:
                merged = dict(existing)
                for provider in incoming:
                    merged[provider.id] = provider
                # Existing order first, so a merge does not reshuffle the
                # registry page on every import.
                providers = [merged[pid] for pid in existing if pid in merged]
                providers += [p for p in incoming if p.id not in existing]

            self._persist(
                ProviderRegistryConfig(version=registry.version, providers=providers)
            )
            logger.info(
                "model_registry_imported",
                extra={
                    "mode": "replace" if replace else "merge",
                    "added": len(added),
                    "updated": len(updated),
                    "total": len(providers),
                },
            )
            return added, updated

    def delete(self, provider_id: str) -> None:
        with self._lock:
            registry = self.load()
            remaining = [p for p in registry.providers if p.id != provider_id]
            if len(remaining) == len(registry.providers):
                raise UnknownProviderError(provider_id)
            self._persist(
                ProviderRegistryConfig(version=registry.version, providers=remaining)
            )

    def _persist(self, registry: ProviderRegistryConfig) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(registry.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self._path)
        self._registry = registry
        # Recorded after the write so the stamp matches what is now on disk;
        # taken before it, the next load() would see a mismatch and re-read the
        # file we just wrote.
        self._stamp = self._file_stamp()
        logger.info(
            "model_registry_persisted", extra={"providers": len(registry.providers)}
        )


model_registry = ModelRegistry()
