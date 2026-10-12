from pathlib import Path
from typing import Iterator, Tuple

from bs4 import BeautifulSoup, Tag

from src.shared.models import Material

from .protocols import DataIntegrityException, ProfileRepository


class ProfileRepositoryXML(ProfileRepository):
    """Read-only profile snapshot; all XML access and material lookup live here."""

    def __init__(self, profile_path: Path) -> None:
        self.profile_path = profile_path
        self.tags: dict[str, Tuple[str, str]] = {
            "experience": ("job", "company"),
            "projects": ("project", "name"),
            "technologies": ("hard_skills", ""),
            "dossier": ("section", "question_id"),
            "cover_letter_examples": ("letter", "id"),
            "coursework": ("academic_course", "code"),
        }
        self._load_profile()

    def _load_profile(self) -> None:
        self._content = self.profile_path.read_text(encoding="utf-8")
        self.profile_xml = BeautifulSoup(self._content, "xml")
        if self.profile_xml.find("profile") is None:
            raise DataIntegrityException("profile")
        self._materials = self._collect_materials()

    def get_cv(self) -> Material:
        cv = self.profile_xml.find("cv")
        if cv is None:
            raise DataIntegrityException("cv")
        return Material(id="profile.cv", text=cv.get_text(separator="\n", strip=True))

    def get_experience(self) -> list[Material]:
        tag_name, tag_id = self.tags["experience"]
        return [
            Material(id=key, text=value)
            for key, value in self._query_xml_block(self.profile_xml, tag_name, tag_id)
        ]

    def get_projects(self) -> list[Material]:
        tag_name, tag_id = self.tags["projects"]
        return [
            Material(id=key, text=value)
            for key, value in self._query_xml_block(self.profile_xml, tag_name, tag_id)
        ]

    def get_skills(self) -> Material:
        skills = self.profile_xml.find(self.tags["technologies"][0])
        if skills is None:
            raise DataIntegrityException("technologies")

        return Material(
            id="profile.cv.hard_skills", text=skills.get_text(separator="\n", strip=True)
        )

    def get_edu(self) -> Material:
        edu = self.profile_xml.find("education")
        if edu is None:
            raise DataIntegrityException("education")

        return Material(id="profile.cv.education", text=edu.get_text(separator="\n", strip=True))

    def get_coursework(self) -> list[Material]:
        tag_name, tag_id = self.tags["coursework"]
        return [
            Material(id=key, text=value)
            for key, value in self._query_xml_block(self.profile_xml, tag_name, tag_id)
        ]

    def get_dossier(self) -> list[Material]:
        tag_name, tag_id = self.tags["dossier"]
        return [
            Material(id=key, text=value)
            for key, value in self._query_xml_block(self.profile_xml, tag_name, tag_id)
        ]

    def get_preferences(self) -> Material:
        preferences = self.profile_xml.find("search_parameters")
        if preferences is None:
            raise DataIntegrityException("search_parameters")

        return Material(
            id="profile.search_preferences", text=preferences.get_text(separator="\n", strip=True)
        )

    def get_cover_letter_examples(self) -> list[Material]:
        tag_name, tag_id = self.tags["cover_letter_examples"]
        return [
            Material(id=key, text=value)
            for key, value in self._query_xml_block(self.profile_xml, tag_name, tag_id)
        ]

    def full_dump(self) -> str:
        return self._content

    def _collect_materials(self) -> dict[str, Material]:
        materials: dict[str, Material] = {}
        for material in (
            self.get_experience()
            + self.get_projects()
            + self.get_dossier()
            + self.get_cover_letter_examples()
            + self.get_coursework()
        ):
            if material.id in materials:
                raise DataIntegrityException(f"duplicate material id: {material.id}")
            materials[material.id] = material
        return materials

    def get_materials(self) -> list[Material]:
        return [material.model_copy(deep=True) for material in self._materials.values()]

    def get_material(self, material_id: str) -> Material:
        material = self._materials.get(material_id)
        if material is None:
            raise DataIntegrityException(material_id)
        return material.model_copy(deep=True)

    @staticmethod
    def _query_xml_block(
        soup: BeautifulSoup, tag_name: str, attr_key: str
    ) -> Iterator[Tuple[str, str]]:
        # Extract all blocks that match tag name. E.g. all <job>, <project>, etc.
        for tag in soup.find_all(tag_name):
            attr_value = str(tag.get(attr_key) or tag.get("id") or "").strip()
            if not attr_value:
                raise DataIntegrityException(f"{tag_name} missing {attr_key}")

            # Traverse upward to capture parent tag names ('commercial', 'experience')
            hierarchy: list[str] = []
            for parent in tag.parents:
                if isinstance(parent, Tag) and parent.name != "[document]":
                    hierarchy.append(parent.name)

            # Invert to root-to-leaf order: ['experience', 'commercial']
            hierarchy.reverse()

            # Form the path identifier: "experience.commercial.job.pixelview"
            key = ".".join(hierarchy + [tag_name, attr_value])
            value = tag.get_text(separator="\n", strip=True)
            # Strip excess leading/trailing whitespace while preserving Markdown lines
            yield key, value
