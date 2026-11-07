"""Transport-only folder wrapper; evidence mutations reuse domain models."""

from src.shared.models.evidence import EvidenceDraft as EvidenceCreate
from src.shared.models.evidence import EvidenceModel, FolderPath
from src.shared.models.evidence import EvidenceUpdate as EvidencePatch
from src.shared.models.evidence import MatchEvidenceUpdate as MatchEvidenceInput


class FolderInput(EvidenceModel):
    path: FolderPath


__all__ = ["EvidenceCreate", "EvidencePatch", "FolderInput", "MatchEvidenceInput"]
