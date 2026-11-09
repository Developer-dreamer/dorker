import tempfile
import unittest
from pathlib import Path

from src.analytics.profile_repository_xml import ProfileRepositoryXML
from src.analytics.protocols import DataIntegrityException, ProfileRepository
from src.shared.models import Material

FIXTURE = Path(__file__).parent / "fixtures/profile.xml"


class ProfileRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRepositoryXML(FIXTURE)

    def test_xml_implements_repository_and_extracts_sections(self):
        self.assertIsInstance(self.profile, ProfileRepository)
        self.assertIn("Candidate in Ukraine", self.profile.get_cv().text)
        self.assertEqual(self.profile.get_preferences().text, "Remote work only.")
        self.assertEqual(self.profile.get_skills().text, "Python\nGo")
        self.assertEqual(self.profile.get_edu().text, "Software engineering studies")
        self.assertEqual(len(self.profile.get_cover_letter_examples()), 2)
        self.assertEqual(len(self.profile.get_dossier()), 1)
        self.assertEqual(len(self.profile.get_coursework()), 2)
        self.assertEqual(self.profile.full_dump(), FIXTURE.read_text())

    def test_material_lookup_uses_same_ids_as_classification(self):
        for material in self.profile.get_materials():
            self.assertEqual(self.profile.get_material(material.id), material)
        self.assertEqual(
            self.profile.get_material("profile.coursework.academic_course.SYSTEMS").text,
            "Systems engineering course",
        )
        with self.assertRaises(DataIntegrityException):
            self.profile.get_material("not.a.material")

    def test_returned_materials_cannot_modify_repository(self):
        material = self.profile.get_projects()[0]
        original = material.text
        self.profile.get_material(material.id).text = "Mutated"
        self.profile.get_materials()[0].text = "Mutated"
        self.assertEqual(self.profile.get_material(material.id).text, original)

    def test_invalid_profile_blocks_fail_explicitly(self):
        for contents in [
            "<wrong_root/>",
            "<profile><projects><project>Missing ID</project></projects></profile>",
            '<profile><projects><project name="same"/><project name="same"/></projects></profile>',
        ]:
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "profile.xml"
                path.write_text(contents)
                with self.assertRaises(DataIntegrityException):
                    ProfileRepositoryXML(path)

    def test_missing_cv_and_preferences_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.xml"
            path.write_text("<profile/>")
            profile = ProfileRepositoryXML(path)
            with self.assertRaises(DataIntegrityException):
                profile.get_cv()
            with self.assertRaises(DataIntegrityException):
                profile.get_preferences()

    def test_legacy_material_paths_are_read_as_ids(self):
        material = Material.model_validate({"path": "profile.project.old", "text": "Saved text"})
        self.assertEqual(material.id, "profile.project.old")
        self.assertEqual(material.model_dump(), {"id": "profile.project.old", "text": "Saved text"})
