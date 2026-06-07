from pydantic import BaseModel, Field


class PromptStructure(BaseModel):
    master_prompt_with_generation: str = Field(...,
                                               description="""
                                               Core instructions for LLM to match job
                                               and applicant based on deep experience.
                                               Contains follow up messages/questions/cover
                                               letter generation instruction
                                               """)
    master_prompt_quick_matching: str = Field(...,
                                              description="""
                                              Simplified master prompt version, for quick matching.
                                              Saves time and tokens by only checking for dead ends:
                                              Remote not as EMEA or worldwide, no salary provided,
                                              etc.
                                              """)
