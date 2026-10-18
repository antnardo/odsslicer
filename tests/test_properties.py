"""DocumentProperties: the document's metadata in meta.xml."""

import datetime as dt
import zipfile

import pytest

from conftest import ods_with_prefixes
from odsslicer import ODSReader

# ---------------------------------------------------------------------------
# ODSReader.properties (DocumentProperties, meta.xml)
# ---------------------------------------------------------------------------


def test_document_properties_reads_existing_metadata(reader):
    p = reader.properties
    assert p.creator == "Antonin Marchand"
    assert p.initial_creator == "Antonin Marchand"
    assert p.generator.startswith("LibreOffice/")


def test_document_properties_unset_fields_are_none_or_empty(reader):
    p = reader.properties
    assert p.title is None
    assert p.subject is None
    assert p.description is None
    assert p.keywords == []
    assert p.custom == {}


def test_document_properties_write_text_fields(writable_reader):
    p = writable_reader.properties
    p.title = "Mon classeur"
    p.subject = "Tests"
    p.description = "Un fichier de test"
    p.creator = "Someone Else"
    assert p.title == "Mon classeur"
    assert p.subject == "Tests"
    assert p.description == "Un fichier de test"
    assert p.creator == "Someone Else"
    # untouched fields still resolve correctly
    assert p.initial_creator == "Antonin Marchand"


def test_document_properties_setting_none_clears_the_field(writable_reader):
    p = writable_reader.properties
    p.title = "Mon classeur"
    p.title = None
    assert p.title is None


def test_document_properties_keywords_read_write(writable_reader):
    p = writable_reader.properties
    p.keywords = ["test", "ods", "python"]
    assert p.keywords == ["test", "ods", "python"]
    p.keywords = ["only-one"]
    assert p.keywords == ["only-one"]  # replaces, doesn't append
    p.keywords = []
    assert p.keywords == []


def test_document_properties_generator_has_no_setter(writable_reader):
    with pytest.raises(AttributeError):
        writable_reader.properties.generator = "odsslicer"


def test_document_properties_custom_dict_access(writable_reader):
    p = writable_reader.properties
    p["Client"] = "Acme Corp"
    assert "Client" in p
    assert p["Client"] == "Acme Corp"
    assert p.custom == {"Client": "Acme Corp"}
    del p["Client"]
    assert "Client" not in p
    with pytest.raises(KeyError):
        p["Client"]
    with pytest.raises(KeyError):
        del p["Client"]


def test_document_properties_custom_typed_values(writable_reader):
    p = writable_reader.properties
    p["as_text"] = "hello"
    p["as_float"] = 42.5
    p["as_bool"] = True
    p["as_date"] = dt.date(2026, 12, 31)
    assert p["as_text"] == "hello" and isinstance(p["as_text"], str)
    assert p["as_float"] == 42.5 and isinstance(p["as_float"], float)
    assert p["as_bool"] is True
    assert p["as_date"] == dt.date(2026, 12, 31)


def test_document_properties_custom_overwrite_changes_type(writable_reader):
    p = writable_reader.properties
    p["Value"] = 1.0
    assert isinstance(p["Value"], float)
    p["Value"] = "now text"
    assert p["Value"] == "now text"


def test_document_properties_custom_invalid_type_raises(writable_reader):
    with pytest.raises(TypeError):
        writable_reader.properties["bad"] = object()


def test_save_round_trip_after_setting_document_properties(writable_reader, tmp_path):
    p = writable_reader.properties
    p.title = "Mon classeur"
    p.keywords = ["a", "b"]
    p["Client"] = "Acme Corp"
    p["Montant"] = 42.5

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).properties
    assert reread.title == "Mon classeur"
    assert reread.keywords == ["a", "b"]
    assert reread.custom == {"Client": "Acme Corp", "Montant": 42.5}
    # cell data/styles from the rest of the document are still intact
    assert ODSReader(out).sheet("Sheet1")["A1"].value == "texte simple"


def test_document_properties_read_and_write_a_meta_xml_with_other_prefixes(tmp_path, test_ods_path):
    # meta.xml is on lxml alone (the feasibility study's prototype): its
    # elements are found by namespace, whatever prefixes the file chose,
    # and a new one comes out with the file's prefix, not a new declaration
    rebound = ods_with_prefixes(
        test_ods_path,
        tmp_path / "rebound.ods",
        {
            "urn:oasis:names:tc:opendocument:xmlns:meta:1.0": "m",
            "http://purl.org/dc/elements/1.1/": "d",
        },
    )
    reader = ODSReader(rebound)
    p = reader.properties
    assert p.creator == "Antonin Marchand"
    assert p.generator.startswith("LibreOffice/")
    p.title = "Mon classeur"
    p["Client"] = "Acme Corp"
    reader.save(tmp_path / "out.ods")

    meta = zipfile.ZipFile(tmp_path / "out.ods").read("meta.xml")
    assert b"<d:title>Mon classeur</d:title>" in meta
    assert b'<m:user-defined m:name="Client">Acme Corp</m:user-defined>' in meta
    assert b"xmlns:dc=" not in meta and b"xmlns:meta=" not in meta
    reread = ODSReader(tmp_path / "out.ods").properties
    assert (reread.title, reread["Client"]) == ("Mon classeur", "Acme Corp")
    assert reread.creator == "Antonin Marchand"
