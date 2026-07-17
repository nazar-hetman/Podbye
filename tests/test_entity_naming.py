"""Entity name disambiguation — concise, meaningful hints."""
from app.services.entity_detector import (
    _disambiguate_names, _shorten_disambiguation_hint,
)
from app.models.smart_entity import SmartEntity


def _e(name, path):
    return SmartEntity(path=path, name=name, entity_type="application",
                       size_bytes=1, file_count=1, folder_count=0)


def test_hint_compression_maps_verbose_containers():
    assert _shorten_disambiguation_hint("Program Files (x86)") == "x86"
    assert _shorten_disambiguation_hint("Program Files") == "64-bit"
    assert _shorten_disambiguation_hint("6.11.1") == "6.11.1"  # version untouched


def test_two_microsofts_disambiguate_by_bitness_not_full_path():
    ents = [
        _e("Microsoft", r"C:\Program Files\Microsoft"),
        _e("Microsoft", r"C:\Program Files (x86)\Microsoft"),
    ]
    _disambiguate_names(ents)
    names = {e.name for e in ents}
    assert names == {"Microsoft (64-bit)", "Microsoft (x86)"}
    # the noisy full segment must be gone
    assert not any("Program Files (x86)" in n for n in names)


def test_version_dirs_still_use_the_version():
    ents = [
        _e("Qt", r"C:\Qt\6.11.1"),
        _e("Qt", r"C:\Qt\6.5.0"),
    ]
    _disambiguate_names(ents)
    assert {e.name for e in ents} == {"Qt (6.11.1)", "Qt (6.5.0)"}


def test_unique_names_are_left_alone():
    ents = [_e("Blender", r"C:\Program Files\Blender")]
    _disambiguate_names(ents)
    assert ents[0].name == "Blender"
