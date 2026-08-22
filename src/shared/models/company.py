from pydantic import BaseModel, ConfigDict, HttpUrl


class Company(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ats_name: str
    name: str
    slug: str
    url: HttpUrl
    tier: int
