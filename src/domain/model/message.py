from pydantic import BaseModel, Field


class BotMessage(BaseModel):
    id: int = Field(..., description="Telegram provided user id.")
    user_name: str
    message: str
    sent: str
