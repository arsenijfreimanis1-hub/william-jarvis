"""Free AI provider gateway — the core that routes every capability to the best free key.

Modules:
- catalog   — what each provider can do, which models, free-tier limits, where to sign up
- keys      — find keys (env, .env, keys.env, ~/.config, macOS Keychain), save, mask
- usage     — SQLite ledger of calls, cooldowns after 429s, headroom vs free limits
- gateway   — capability routing (code / chat / reason / long_context / fast) + Ollama fallback
- speech    — STT (Groq Whisper / HF Whisper / local whisper.cpp) and TTS (HF / macOS say)
- vectors   — embeddings (Gemini / HF / Ollama) + vector memory (Pinecone / Supabase / local)
- cad       — OpenSCAD + Blender script generation, local compile, HF Spaces mesh download
- scout     — Key Scout / Quota Keeper agent jobs (scan, probe, rollup, status replies)
"""
