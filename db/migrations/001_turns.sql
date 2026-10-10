-- One row per agent reply, written by agent/telemetry.py.
-- Metric definitions: design doc §6. Durations in milliseconds, computed by LiveKit.
-- No transcript text yet: it is personal data and needs redaction first (design §11).

create table if not exists turns (
    id                     bigint generated always as identity primary key,
    call_id                text        not null,
    turn_index             int         not null,
    channel                text        not null check (channel in ('browser', 'phone')),
    language               text        not null,
    providers              jsonb       not null,           -- {"stt": ..., "llm": ..., "tts": ...}
    cold_call              boolean     not null,           -- first call in the worker process

    user_speech_start_at   timestamptz,                    -- null for the greeting
    user_speech_end_at     timestamptz,                    -- VAD: caller stopped speaking
    end_of_turn_ms         real,                           -- speech end -> turn committed
    stt_final_ms           real,                           -- speech end -> final transcript
    stt_confidence         real,

    llm_ttft_ms            real,                           -- LLM request -> first token
    llm_first_sentence_ms  real,                           -- LLM start -> first sentence to TTS
    tts_ttfb_ms            real,                           -- first text to TTS -> first audio
    agent_audio_start_at   timestamptz,
    voice_to_voice_ms      real,                           -- speech end -> agent audio starts

    llm_prompt_tokens      int,                            -- tokens for this reply's LLM requests
    llm_cached_tokens      int,                            -- prompt tokens served from cache
    llm_reasoning_tokens   int,                            -- hidden thinking tokens
    llm_completion_tokens  int,

    interrupted            boolean     not null default false,
    interrupt_detect_ms    real,                           -- caller onset -> interruption decided
    interrupt_stop_ms      real,                           -- caller onset -> agent audio stopped

    tool_calls             jsonb       not null default '[]', -- [{name, duration_ms, is_error}]
    created_at             timestamptz not null default now(),

    unique (call_id, turn_index)
);

create index if not exists turns_created_at_idx on turns (created_at);
