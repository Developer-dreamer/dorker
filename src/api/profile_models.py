"""HTTP names for the shared domain commands; the wire shapes are unchanged."""

from src.shared.models.profile import EvidenceLinks as EvidenceLinksInput
from src.shared.models.profile import ProfileUpdate as ProfilePatch

__all__ = ["EvidenceLinksInput", "ProfilePatch"]
