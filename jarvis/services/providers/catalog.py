"""Catalog of free AI providers William can route to, grouped by capability."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Capability = Literal[
    "chat",          # everyday conversation, personality, memory recall
    "fast",          # boilerplate / quick drafts — latency first
    "code",          # app + website implementation, fill-in-the-middle
    "reason",        # harder multi-step reasoning
    "long_context",  # whole-codebase / big-document prompts
    "cad",           # OpenSCAD / Blender script generation
    "stt",           # speech-to-text
    "tts",           # text-to-speech
    "embed",         # text embeddings for vector memory
    "vector",        # vector database storage/query
    "mesh3d",        # image/text → 3D mesh (HF Spaces)
]

ALL_CAPABILITIES: tuple[str, ...] = (
    "chat",
    "fast",
    "code",
    "reason",
    "long_context",
    "cad",
    "stt",
    "tts",
    "embed",
    "vector",
    "mesh3d",
)


class FreeTier(BaseModel):
    """Approximate free-tier ceilings used for headroom checks (never exact billing)."""

    requests_per_minute: int | None = None
    requests_per_day: int | None = None
    tokens_per_minute: int | None = None
    tokens_per_day: int | None = None
    note: str = ""


class ProviderSpec(BaseModel):
    id: str
    name: str
    kind: Literal["llm", "speech", "vector", "spaces", "local"] = "llm"
    free: bool = True
    env_keys: list[str] = Field(default_factory=list)
    settings_attr: str | None = None
    key_prefixes: list[str] = Field(default_factory=list)
    key_min_length: int = 16
    base_url: str = ""
    openai_compatible: bool = False
    capabilities: list[str] = Field(default_factory=list)
    models: dict[str, str] = Field(default_factory=dict)
    free_tier: FreeTier = Field(default_factory=FreeTier)
    signup_url: str = ""
    docs_url: str = ""
    extra_settings: list[str] = Field(default_factory=list)

    def model_for(self, capability: str) -> str | None:
        return self.models.get(capability) or self.models.get("default")

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


PROVIDERS: dict[str, ProviderSpec] = {
    "gemini": ProviderSpec(
        id="gemini",
        name="Google AI Studio (Gemini)",
        env_keys=["GEMINI_API_KEY", "GOOGLE_AI_STUDIO_API_KEY", "GOOGLE_API_KEY", "JARVIS_GEMINI_API_KEY"],
        settings_attr="gemini_api_key",
        key_prefixes=["AIza"],
        key_min_length=30,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        openai_compatible=True,
        capabilities=["chat", "fast", "code", "reason", "long_context", "cad", "embed"],
        models={
            "default": "gemini-2.5-flash",
            "fast": "gemini-2.5-flash-lite",
            "chat": "gemini-2.5-flash-lite",
            "code": "gemini-2.5-flash",
            "reason": "gemini-2.5-flash",
            "long_context": "gemini-2.5-flash",
            "cad": "gemini-2.5-flash",
            "embed": "gemini-embedding-001",
        },
        free_tier=FreeTier(requests_per_minute=10, requests_per_day=250, tokens_per_minute=250_000,
                           note="1M-token context; feed whole codebases and wireframes"),
        signup_url="https://aistudio.google.com/apikey",
        docs_url="https://ai.google.dev/gemini-api/docs/openai",
    ),
    "mistral": ProviderSpec(
        id="mistral",
        name="Mistral AI (Codestral)",
        env_keys=["MISTRAL_API_KEY", "JARVIS_MISTRAL_API_KEY"],
        settings_attr="mistral_api_key",
        key_min_length=24,
        base_url="https://api.mistral.ai/v1",
        openai_compatible=True,
        capabilities=["chat", "fast", "code", "reason", "cad", "embed"],
        models={
            "default": "mistral-small-latest",
            "fast": "mistral-small-latest",
            "chat": "mistral-small-latest",
            "code": "codestral-latest",
            "cad": "codestral-latest",
            "reason": "mistral-medium-latest",
            "embed": "mistral-embed",
        },
        free_tier=FreeTier(requests_per_minute=60, tokens_per_minute=500_000, tokens_per_day=1_000_000_000 // 30,
                           note="Experiment tier; Codestral is built for code + fill-in-the-middle"),
        signup_url="https://console.mistral.ai/api-keys",
        docs_url="https://docs.mistral.ai/api/",
    ),
    "codestral": ProviderSpec(
        id="codestral",
        name="Mistral Codestral (dedicated endpoint)",
        env_keys=["CODESTRAL_API_KEY", "JARVIS_CODESTRAL_API_KEY"],
        settings_attr="codestral_api_key",
        key_min_length=24,
        base_url="https://codestral.mistral.ai/v1",
        openai_compatible=True,
        capabilities=["code", "cad", "fast"],
        models={"default": "codestral-latest"},
        free_tier=FreeTier(requests_per_minute=30, requests_per_day=2000, note="Separate free Codestral key"),
        signup_url="https://console.mistral.ai/codestral",
        docs_url="https://docs.mistral.ai/capabilities/code_generation/",
    ),
    "groq": ProviderSpec(
        id="groq",
        name="Groq Cloud",
        env_keys=["GROQ_API_KEY", "JARVIS_GROQ_API_KEY"],
        settings_attr="groq_api_key",
        key_prefixes=["gsk_"],
        key_min_length=40,
        base_url="https://api.groq.com/openai/v1",
        openai_compatible=True,
        capabilities=["chat", "fast", "code", "reason", "cad", "stt"],
        models={
            "default": "llama-3.3-70b-versatile",
            "fast": "llama-3.1-8b-instant",
            "chat": "llama-3.1-8b-instant",
            "code": "llama-3.3-70b-versatile",
            "cad": "llama-3.3-70b-versatile",
            "reason": "llama-3.3-70b-versatile",
            "stt": "whisper-large-v3-turbo",
        },
        free_tier=FreeTier(requests_per_minute=30, requests_per_day=1000, tokens_per_minute=12_000,
                           note="Ultra-fast; Whisper transcription is free"),
        signup_url="https://console.groq.com/keys",
        docs_url="https://console.groq.com/docs/openai",
    ),
    "huggingface": ProviderSpec(
        id="huggingface",
        name="Hugging Face Inference API",
        env_keys=["HF_TOKEN", "HUGGINGFACE_API_KEY", "HUGGINGFACEHUB_API_TOKEN", "JARVIS_HUGGINGFACE_API_KEY"],
        settings_attr="huggingface_api_key",
        key_prefixes=["hf_"],
        key_min_length=30,
        base_url="https://router.huggingface.co",
        openai_compatible=True,
        capabilities=["chat", "code", "stt", "tts", "embed", "mesh3d"],
        models={
            "default": "meta-llama/Llama-3.1-8B-Instruct",
            "chat": "meta-llama/Llama-3.1-8B-Instruct",
            "code": "Qwen/Qwen2.5-Coder-32B-Instruct",
            "stt": "openai/whisper-large-v3",
            "tts": "facebook/mms-tts-eng",
            "embed": "sentence-transformers/all-MiniLM-L6-v2",
        },
        free_tier=FreeTier(requests_per_day=1000, note="Monthly free inference credits; Spaces via gradio_client"),
        signup_url="https://huggingface.co/settings/tokens",
        docs_url="https://huggingface.co/docs/inference-providers",
    ),
    "openrouter": ProviderSpec(
        id="openrouter",
        name="OpenRouter (free tier routing)",
        env_keys=["OPENROUTER_API_KEY", "JARVIS_OPENROUTER_API_KEY"],
        settings_attr="openrouter_api_key",
        key_prefixes=["sk-or-"],
        key_min_length=40,
        base_url="https://openrouter.ai/api/v1",
        openai_compatible=True,
        capabilities=["chat", "reason", "code", "long_context"],
        models={"default": "meta-llama/llama-3.3-70b-instruct:free"},
        free_tier=FreeTier(requests_per_minute=20, requests_per_day=50,
                           note="Global personality system prompt lives here; :free models only"),
        signup_url="https://openrouter.ai/settings/keys",
        docs_url="https://openrouter.ai/docs/quickstart",
    ),
    "pinecone": ProviderSpec(
        id="pinecone",
        name="Pinecone (vector memory)",
        kind="vector",
        env_keys=["PINECONE_API_KEY", "JARVIS_PINECONE_API_KEY"],
        settings_attr="pinecone_api_key",
        key_prefixes=["pcsk_"],
        key_min_length=30,
        capabilities=["vector"],
        free_tier=FreeTier(note="Starter: 2 GB storage, 1 serverless index"),
        signup_url="https://app.pinecone.io/",
        docs_url="https://docs.pinecone.io/reference/api/introduction",
        extra_settings=["JARVIS_PINECONE_INDEX_HOST"],
    ),
    "supabase": ProviderSpec(
        id="supabase",
        name="Supabase (pgvector memory)",
        kind="vector",
        env_keys=["SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY", "JARVIS_SUPABASE_SERVICE_KEY"],
        settings_attr="supabase_service_key",
        key_min_length=30,
        capabilities=["vector"],
        free_tier=FreeTier(note="500 MB Postgres + pgvector; project pauses after 1 week idle"),
        signup_url="https://supabase.com/dashboard",
        docs_url="https://supabase.com/docs/guides/ai/vector-columns",
        extra_settings=["JARVIS_SUPABASE_URL"],
    ),
    "ollama": ProviderSpec(
        id="ollama",
        name="Ollama (local)",
        kind="local",
        base_url="http://127.0.0.1:11434",
        capabilities=["chat", "fast", "code", "reason", "long_context", "cad", "embed"],
        models={"default": "llama3.1:8b", "embed": "nomic-embed-text"},
        free_tier=FreeTier(note="Always available on the Mac Mini; private; final fallback"),
        docs_url="https://github.com/ollama/ollama/blob/main/docs/api.md",
    ),
}

# Ordered preference per capability when running free_cloud_first. Ollama is always appended.
CHAINS: dict[str, list[str]] = {
    "chat": ["openrouter", "groq", "gemini", "mistral", "huggingface"],
    "fast": ["groq", "gemini", "mistral", "codestral", "openrouter"],
    "code": ["codestral", "mistral", "gemini", "groq", "openrouter", "huggingface"],
    "reason": ["gemini", "groq", "openrouter", "mistral"],
    "long_context": ["gemini", "openrouter", "groq", "mistral"],
    "cad": ["gemini", "codestral", "mistral", "groq", "openrouter"],
    "stt": ["groq", "huggingface"],
    "tts": ["huggingface"],
    "embed": ["gemini", "mistral", "huggingface"],
    "vector": ["pinecone", "supabase"],
    "mesh3d": ["huggingface"],
}


def get(provider_id: str) -> ProviderSpec | None:
    return PROVIDERS.get(provider_id)


def providers_for(capability: str) -> list[ProviderSpec]:
    chain = CHAINS.get(capability, [])
    out = [PROVIDERS[p] for p in chain if p in PROVIDERS]
    if capability in PROVIDERS["ollama"].capabilities:
        out.append(PROVIDERS["ollama"])
    return out


def capability_groups() -> dict[str, list[str]]:
    """Human-facing grouping that mirrors the master list (coding, speech, memory, 3D)."""
    return {
        "Website & App Development": ["gemini", "mistral", "codestral", "groq"],
        "Speech (STT / TTS)": ["groq", "huggingface"],
        "Memory & Personality": ["openrouter", "gemini", "huggingface", "pinecone", "supabase"],
        "3D CAD & Modeling": ["gemini", "groq", "huggingface"],
        "Local fallback": ["ollama"],
    }
