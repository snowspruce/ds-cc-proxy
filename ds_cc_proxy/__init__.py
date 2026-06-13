# ds-cc-proxy — DeepSeek Anthropic API compatibility proxy
#
# Key features:
#   1. Request-side: inject empty thinking blocks for tool_use assistant messages missing them
#   2. Request-side: convert unsupported thinking modes (disabled → enabled + budget_tokens=2048)
#   3. Response-side: strip unexpected thinking/thinking_delta/signature_delta SSE events
#   4. Sub-agent routing: redirect thinking=disabled requests to Flash endpoint for cost savings
#   5. Cost tracking: log token usage per request with subagent/primary distinction
#   6. Graceful shutdown: 5s drain window + shutdown signal to reject new requests

from ds_cc_proxy._version import VERSION
from ds_cc_proxy.proxy import create_app

__all__ = ["VERSION", "create_app"]
