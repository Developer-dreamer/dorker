import asyncio

from google import genai
from google.genai import types

from src.config.logger import Logger
from src.domain.interface.abc_ai_client import AIClient


class GeminiFlashClient(AIClient):
    def __init__(self, api_key: str, logger: Logger):
        self.client = genai.Client(api_key=api_key)
        self.logger = logger
        self.available_models = ["gemini-3.5-flash",
                                 "gemini-3.1-flash-lite", "gemini-2.5-flash"]

        self.system_instruction = ""

    def model_init(self, system_instruction: str, data: str = "") -> None:
        self.system_instruction = system_instruction

    def model_reset(self) -> None:
        self.system_instruction = ""

    async def generate_json(self, prompt: str) -> str:
        if self.system_instruction == "":
            raise ValueError("System instruction was not set. Call model_init() first.")

        last_exception = None

        for model_name in self.available_models:
            for attempt in range(1, 4):
                try:
                    response = await self.client.aio.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=self.system_instruction,
                            response_mime_type="application/json",
                            temperature=0.1
                        )
                    )

                    if response and response.text:
                        return str(response.text)

                except Exception as e:
                    last_exception = e
                    self.logger.warning(
                        f"Model {model_name} failed on attempt #{attempt}. Error: {str(e)}"
                    )

                    sleep_time = min(2 ** attempt, 10)
                    self.logger.info(f"Switching fallback. Waiting {sleep_time}s before trying next model...")
                    await asyncio.sleep(sleep_time)

        self.logger.warning("All available Gemini models completely failed to respond.")
        if last_exception:
            raise last_exception
        raise RuntimeError("LLM_INFRASTRUCTURE_DOWN")

class GeminiProClient(AIClient):
    def __init__(self, api_key: str, logger: Logger):
        self.client = genai.Client(api_key=api_key)
        self.logger = logger
        self.model = "gemini-3.1-pro-preview"
        self.cache: types.CachedContent | None = None
        self.system_instruction = ""

    def model_init(self, system_instruction: str, data: str = "") -> None:
        if data == "":
            self.system_instruction = system_instruction
            return

        self.cache = self.client.caches.create(
            model=self.model,
            config=types.CreateCachedContentConfig(
                display_name='candidate profile', # used to identify the cache
                system_instruction=system_instruction,
                contents=data,
                ttl="60s",
            )
        )

    def model_reset(self) -> None:
        if not self.cache or not self.cache.name:
            return

        self.client.caches.delete(name=self.cache.name)
        self.cache = None

    async def generate_json(self, prompt: str) -> str:
        if not self.cache and self.system_instruction == "":
            raise ValueError("System instruction was not set. Call model_init() first.")

        last_exception = None

        for attempt in range(1, 4):
            try:
                config = types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    )
                if self.cache:
                    config.cached_content = self.cache.name

                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config
                )

                if response and response.text:
                    return response.text

            except Exception as e:
                last_exception = e
                self.logger.warning(
                    f"Model {self.model} failed on attempt #{attempt}. Error: {str(e)}"
                )

                sleep_time = min(2 ** attempt, 10)
                self.logger.info(f"Switching fallback. Waiting {sleep_time}s before trying next model...")
                await asyncio.sleep(sleep_time)

        self.logger.warning("All available Gemini models completely failed to respond.")
        if last_exception:
            raise last_exception
        raise RuntimeError("LLM_INFRASTRUCTURE_DOWN")
