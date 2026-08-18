from openai import OpenAI

from src.scraping.models import Job

from .models import GatekeeperDecision, MatchedJob

client = OpenAI()

def reject(job: Job) -> bool:
    job_payload = job.model_dump_json(
        include={"title", "department", "team", "employment_type"},
        exclude_none=True
    )

    completion = client.chat.completions.parse(
        model="gpt-5.4-nano",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an ultra-fast technical recruitment gatekeeper. "
                    "Your ONLY task is to aggressively filter out completely irrelevant job descriptions. "
                    "Candidate constraints: Developer/Engineering roles only. "
                    "Evaluate the job payload and return true if relevant (software/ai developer/engineer etc.), false if marketing, sales, or other non-technical."
                )
            },
            {
                "role": "user",
                "content": job_payload
            }
        ],
        response_format=GatekeeperDecision,
    )

    return completion.choices[0].message.parsed.is_relevant



MASTER_PROMPT = ""
try:
    with open("./prompt/ranking_prompt.md", "r") as f:
        MASTER_PROMPT = f.read()
        if MASTER_PROMPT == "":
            raise AttributeError
except FileNotFoundError as e:
    print("File wasn't found, please create and fill it first.")
    exit(-2)
except AttributeError as e:
    print("File is empty, please fill it first.")



def rank(job: Job) -> MatchedJob | None:
    job_payload = job.model_dump_json(
            exclude={"global_id", "url", "ats_type", "ats_id", "lat", "lon", "is_remote", "requisition_id", "apply_url", "raw"}
        )

    completion = client.chat.completions.parse(
            model="gpt-5.6-terra",
            messages=[
                {
                    "role": "system",
                    "content": MASTER_PROMPT
                },
                {
                    "role": "user",
                    "content": job_payload
                }
            ],
            response_format=MatchedJob,
        )


    return completion.choices[0].message.parsed
