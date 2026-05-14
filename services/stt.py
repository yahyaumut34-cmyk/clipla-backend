from config import config


# Dummy OpenAI STT class (emergentintegrations yerine geçici çözüm)
class OpenAISpeechToText:
    def __init__(self, *args, **kwargs):
        pass

    async def transcribe(self, *args, **kwargs):
        class DummyResponse:
            text = "dummy transcription"
        return DummyResponse()


class STTService:
    """Speech-to-text dummy service"""

    def __init__(self):
        self.stt = OpenAISpeechToText(api_key=config.EMERGENT_LLM_KEY)

    async def transcribe(self, audio_path: str, language: str = None) -> str:
        """Fake transcription"""
        return "dummy transcription"


stt_service = STTService()
