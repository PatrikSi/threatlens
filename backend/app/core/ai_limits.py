"""Application bounds shared by AI configuration and request budgeting.

These limits bound ThreatLens requests; each selected model may support less.
"""

MAX_AI_COMPLETION_TOKENS = 131_072
AI_CONTEXT_PROTOCOL_OVERHEAD_TOKENS = 384
MIN_AI_CONTEXT_INPUT_TOKENS = 512
