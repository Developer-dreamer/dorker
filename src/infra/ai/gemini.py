from google import genai
from google.genai import types


class GeminiFlashClient:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)

    async def generate_json(self, prompt: str, system_instruction: str) -> str:
        response = await self.client.aio.models.generate_content(
            model='gemini-3.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.0
            )
        )
        return str(response.text)
