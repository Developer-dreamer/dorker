import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.analytics.generation.summary import JobSummaryOutput
from src.analytics.generation.summary_openai import OpenAISummaryGenerator


class SummaryOpenAITests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_luna_and_only_job_context_with_existing_json_shape(self):
        result = JobSummaryOutput(summary="Scope", pros="Opportunities", cons="Risks")
        parse = AsyncMock(return_value=SimpleNamespace(status="completed", output_parsed=result))
        client = SimpleNamespace(responses=SimpleNamespace(parse=parse), close=AsyncMock())
        generator = OpenAISummaryGenerator(client)
        job = SimpleNamespace(title="Backend", location="Remote", description="Python systems")
        self.assertEqual(await generator.generate(job), result.model_dump_json())
        kwargs = parse.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-6-luna")
        self.assertEqual(kwargs["text_format"], JobSummaryOutput)
        self.assertIn("Python systems", kwargs["input"])
        self.assertFalse(kwargs["store"])
        await generator.close()
        client.close.assert_awaited_once()

    async def test_incomplete_or_refused_response_is_not_saved_as_a_summary(self):
        for status, parsed in [("incomplete", None), ("completed", None)]:
            with self.subTest(status=status):
                client = SimpleNamespace(
                    responses=SimpleNamespace(
                        parse=AsyncMock(
                            return_value=SimpleNamespace(status=status, output_parsed=parsed)
                        )
                    )
                )
                with self.assertRaises(ValueError):
                    await OpenAISummaryGenerator(client).generate(
                        SimpleNamespace(title="Role", location=None, description="Description")
                    )
