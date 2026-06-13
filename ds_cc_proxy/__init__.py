# ds-cc-proxy — DeepSeek Anthropic API compatibility proxy
#
# Bidirectional proxy fixes for DeepSeek V4 Anthropic API compatibility:
#   1. Request-side: inject empty thinking blocks for tool_use assistant messages missing them
#   2. Request-side: convert unsupported thinking modes (disabled → enabled)
#   3. Response-side: strip unexpected thinking/thinking_delta/signature_delta SSE events

from ds_cc_proxy._version import VERSION
from ds_cc_proxy.proxy import create_app

__all__ = ["VERSION", "create_app"]
