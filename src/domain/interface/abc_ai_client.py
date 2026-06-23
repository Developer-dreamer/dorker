from abc import ABC, abstractmethod


class AIClient(ABC):

    @abstractmethod
    def model_init(self, system_instruction: str, data: str = "") -> None:
        pass

    @abstractmethod
    def model_reset(self) -> None:
        pass

    @abstractmethod
    async def generate_json(self, prompt: str) -> str:
        pass
