import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from jarvis.paths import BACKUPS_DIR, LOGS_DIR, MODELS_DIR, ROOT, ensure_state_dirs, resolve_data_dir

_PLACEHOLDER_KEYS = frozenset({"cursor_...", "cursor_…", "your_key_here", "changeme"})


def _read_env_file(key: str) -> str:
    path = ROOT / ".env"
    if not path.is_file():
        return ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith(f"{key}="):
            continue
        return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def is_cursor_key_valid(key: str) -> bool:
    if not key:
        return False
    if key.lower() in _PLACEHOLDER_KEYS or key.endswith("..."):
        return False
    return len(key) >= 24 and (
        key.startswith("cursor_") or key.startswith("crsr_") or key.startswith("key_")
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_", env_file=ROOT / ".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8787
    # Device role: `mini` (always-on control plane) or `macbook` (intake + thought). Same codebase.
    role: str = "mini"
    device_name: str = ""
    # Device link (docs/LINK_PROTOCOL.md). MacBook dials the Mini; Mini accepts on /ws/link.
    link_enabled: bool = True
    link_peer_url: str = ""  # macbook: ws://<mini-tailscale-or-lan>:8787/ws/link
    link_heartbeat_seconds: int = 10
    link_timeout_seconds: int = 30
    link_backlog: int = 500
    # Resource governor
    governor_enabled: bool = True
    governor_interval_seconds: int = 5
    # Brain persona file override (defaults to jarvis/brain/persona/<role>.md)
    persona_file: str = ""
    data_dir: Path = Field(default_factory=resolve_data_dir)
    workspace_dir: Path = ROOT
    state_root: Path = Field(default_factory=lambda: resolve_data_dir().parent)
    logs_dir: Path = LOGS_DIR
    models_dir: Path = MODELS_DIR
    backups_dir: Path = BACKUPS_DIR
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_vision_model: str = "moondream"
    agent_name: str = "William Agent"
    openclaw_gateway_url: str = "http://127.0.0.1:18789"
    macos_helper_url: str = "http://127.0.0.1:8788"
    netatmo_client_id: str = ""
    netatmo_client_secret: str = ""
    public_webhook_url: str = ""
    cursor_model: str = "composer-2.5"
    cursor_api_key: str = ""
    timezone: str = "Europe/Amsterdam"
    briefing_hour: int = 8
    learning_report_interval_hours: int = 4
    music_provider: str = "spotify"
    intent_llm_enabled: bool = False
    intent_regex_threshold: float = 0.85
    memory_compress_interval_hours: int = 6
    voice_watchdog_interval_minutes: int = 2
    notion_api_key: str = ""
    notion_parent_page_id: str = ""
    notion_export_interval_hours: int = 12
    screen_watch_enabled: bool = False
    screen_capture_interval_seconds: int = 10
    screen_summary_window_seconds: int = 60
    screen_screenshot_retention_minutes: int = 30
    screen_observer_interval_seconds: int = 60
    screen_semantic_anchor_threshold: float = 0.5
    screenpipe_bridge_enabled: bool = False
    screenpipe_base_url: str = "http://127.0.0.1:3030"
    popup_handler_enabled: bool = True
    popup_max_attempts: int = 3
    popup_watchdog_interval_seconds: int = 45
    auto_full_access: bool = False
    remote_control_enabled: bool = False
    cursor_trace_enabled: bool = True
    sleep_junk_threshold_seconds: int = 45
    sleep_background_speech_seconds: int = 50
    vigil_enabled: bool = False
    vigil_gateway_url: str = "https://api.vigil.wtf/921f5773-4652-41e4-98f2-3c6ec36d0b3d"
    vigil_api_url: str = "https://app.vigil-agent.com"
    vigil_api_key: str = ""
    vigil_agent_id: str = "william"
    vigil_proxy_enabled: bool = True
    vigil_proxy_url: str = "https://api.vigil.wtf/921f5773-4652-41e4-98f2-3c6ec36d0b3d"
    vigil_proxy_provider: str = "anthropic"
    vigil_proxy_openai_model: str = "gpt-4o-mini"
    vigil_proxy_anthropic_model: str = "claude-sonnet-4-6"
    vigil_proxy_max_tokens: int = 1024
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    build_parallel: int = 3
    worker_parallel: int = 6
    terminal_parallel: int = 4
    free_first: bool = True
    build_projects_dir: Path = Path.home() / "Projects"
    external_skills_enabled: bool = True
    mcp_enabled: bool = True
    mcp_config_path: str = ""
    github_token: str = ""
    github_owner: str = ""
    github_org: str = ""
    william_hub_repo: str = "william-hub"
    github_projects_private: bool = True
    cursor_runtime: str = "local"
    compute_cloud_workers: int = 3
    github_sync_interval_hours: int = 2
    # LAN fleet (same WiFi) — Mini is control plane; peers heartbeat over LAN
    fleet_token: str = ""
    fleet_heartbeat_timeout_seconds: int = 90
    fleet_mini_name: str = "Mac Mini"
    fleet_mini_lan_host: str = "127.0.0.1"
    fleet_macbook_name: str = "MacBook"
    fleet_macbook_lan_host: str = ""
    fleet_pc_name: str = "Windows PC"
    fleet_pc_lan_host: str = ""
    fleet_pc_mac: str = ""
    fleet_wol_broadcast: str = "255.255.255.255"
    fleet_wol_port: int = 9
    # Free AI provider gateway — one backend routes every capability to the best free key.
    # free_cloud_first | local_first | local_only
    gateway_mode: str = "free_cloud_first"
    gateway_timeout_seconds: int = 90
    gateway_log_prompts: bool = False
    gemini_api_key: str = ""
    mistral_api_key: str = ""
    codestral_api_key: str = ""
    groq_api_key: str = ""
    huggingface_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    pinecone_api_key: str = ""
    pinecone_index_host: str = ""
    supabase_url: str = ""
    supabase_service_key: str = ""
    keys_file: Path = Path.home() / ".config" / "jarvis" / "keys.env"
    keychain_lookup_enabled: bool = True
    provider_key_scan_interval_minutes: int = 30
    provider_probe_interval_hours: int = 6
    semantic_memory_enabled: bool = True
    personality_prompt: str = ""
    cad_output_dir: Path = Field(default_factory=lambda: ROOT / "exports" / "cad")
    hf_mesh_space: str = "stabilityai/TripoSR"
    app_icons_enabled: bool = True

    def resolved_vigil_api_key(self) -> str:
        for candidate in (
            self.vigil_api_key,
            os.environ.get("VIGIL_API_KEY", ""),
            _read_env_file("VIGIL_API_KEY"),
            _read_env_file("JARVIS_VIGIL_API_KEY"),
        ):
            if candidate and candidate.startswith("vgl_") and len(candidate) >= 20:
                return candidate
        return ""

    def resolved_openai_api_key(self) -> str:
        for candidate in (
            self.openai_api_key,
            os.environ.get("OPENAI_API_KEY", ""),
            _read_env_file("OPENAI_API_KEY"),
            _read_env_file("JARVIS_OPENAI_API_KEY"),
        ):
            if candidate and len(candidate) >= 20 and not candidate.endswith("..."):
                return candidate
        return ""

    def resolved_anthropic_api_key(self) -> str:
        for candidate in (
            self.anthropic_api_key,
            os.environ.get("ANTHROPIC_API_KEY", ""),
            _read_env_file("ANTHROPIC_API_KEY"),
            _read_env_file("JARVIS_ANTHROPIC_API_KEY"),
        ):
            if candidate and len(candidate) >= 20 and not candidate.endswith("..."):
                return candidate
        return ""

    def resolved_cursor_api_key(self) -> str:
        for candidate in (
            self.cursor_api_key,
            os.environ.get("CURSOR_API_KEY", ""),
            os.environ.get("JARVIS_CURSOR_API_KEY", ""),
            _read_env_file("CURSOR_API_KEY"),
            _read_env_file("JARVIS_CURSOR_API_KEY"),
        ):
            if is_cursor_key_valid(candidate):
                return candidate
        return ""

    def resolved_notion_api_key(self) -> str:
        candidates = [
            self.notion_api_key,
            os.environ.get("NOTION_API_KEY", ""),
            _read_env_file("NOTION_API_KEY"),
            _read_env_file("JARVIS_NOTION_API_KEY"),
        ]
        keyfile = Path.home() / ".config" / "notion" / "api_key"
        if keyfile.is_file():
            candidates.append(keyfile.read_text(encoding="utf-8").strip())
        for candidate in candidates:
            if not candidate or len(candidate) < 20:
                continue
            if candidate.startswith("secret_...") or "your_key" in candidate.lower():
                continue
            return candidate
        return ""

    def notion_configured(self) -> bool:
        return bool(self.resolved_notion_api_key() and self.notion_parent_page_id)

    def cursor_configured(self) -> bool:
        return bool(self.resolved_cursor_api_key())


settings = Settings()
ensure_state_dirs(settings.data_dir)
