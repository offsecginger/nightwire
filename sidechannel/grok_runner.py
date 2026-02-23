"""Grok API runner for nova AI assistant integration."""

import os
from typing import Optional, Tuple

import aiohttp
import structlog

logger = structlog.get_logger()

GROK_API_URL = "https://api.x.ai/v1/chat/completions"


class GrokRunner:
    """Manages Grok API execution for nova AI assistant."""

    def __init__(self):
        self.api_key = os.environ.get("GROK_API_KEY")
        if not self.api_key:
            logger.warning("grok_api_key_not_found")

    async def ask_jarvis(
        self,
        message: str,
        timeout: int = 60
    ) -> Tuple[bool, str]:
        """
        Send a query to Grok as nova AI assistant.

        Args:
            message: The user's message
            timeout: Request timeout in seconds

        Returns:
            Tuple of (success, response)
        """
        if not self.api_key:
            return False, "nova is not configured. Missing GROK_API_KEY."

        # Clean the message - remove nova prefix variations
        clean_message = message.strip()
        msg_lower = clean_message.lower()
        for variant in ["nova:", "nova,", "nova ", "hey nova ", "hi nova ", "ok nova "]:
            if msg_lower.startswith(variant):
                clean_message = clean_message[len(variant):].strip()
                break

        if clean_message.lower() == "nova" or not clean_message:
            clean_message = "Hello, how can you help me?"

        system_prompt = """You are nova, an intelligent AI development assistant integrated into a Signal messaging bot called sidechannel.

Personality:
- Professional yet friendly
- Concise and helpful
- Technical but approachable
- Direct and efficient

Capabilities:
- Answer questions on any topic
- Provide technical guidance and recommendations
- Help with research and analysis
- Give coding advice and best practices

When responding:
- Be concise (responses go through Signal messages)
- Lead with the answer, then provide context if needed
- For coding/development tasks, remind the user that /select, /ask, and /do commands are available for hands-on Claude integration
- Be helpful and informative without being verbose

Response Style:
- Clear and organized
- Use bullet points for lists
- Keep responses under 4000 characters
- No emojis unless specifically requested"""

        payload = {
            "model": "grok-3-latest",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": clean_message}
            ],
            "temperature": 0.7,
            "max_tokens": 1024
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    GROK_API_URL,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        response = data["choices"][0]["message"]["content"]
                        logger.info("grok_response_success", length=len(response))

                        # Truncate if too long for Signal
                        if len(response) > 4000:
                            response = response[:4000] + "\n\n[Response truncated...]"

                        return True, response
                    else:
                        error_text = await resp.text()
                        logger.error("grok_api_error", status=resp.status, error=error_text[:500])
                        return False, f"nova encountered an error (status {resp.status}). Please try again."

        except aiohttp.ClientTimeout:
            logger.warning("grok_timeout", timeout=timeout)
            return False, "That query required more processing time than anticipated. Perhaps try a more specific question?"

        except Exception as e:
            logger.error("grok_exception", error=str(e))
            return False, "I'm experiencing a temporary issue. Please try again."


# Global instance
_grok_runner: Optional[GrokRunner] = None


def get_grok_runner() -> GrokRunner:
    """Get or create the global Grok runner instance."""
    global _grok_runner
    if _grok_runner is None:
        _grok_runner = GrokRunner()
    return _grok_runner
