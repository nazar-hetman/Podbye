"""A program's publisher must be readable whatever language its resources are in.

Version resources are stored per language. The startup detector only ever
looked in three blocks - US English (two code pages) and language-neutral -
so a program whose resources are, say, Ukrainian, German or Japanese only
reported no publisher at all. The Startups screen then said "Podbye could not
verify the publisher for this entry" and put it at Review, on a machine
running a non-English Windows with its local software.

The file lists which languages it carries in \\VarFileInfo\\Translation; those
are now asked first, and the three old blocks stay as the fallback.
"""
from app.services.startup_detector import _version_subblocks


def test_the_files_own_language_is_asked_first():
    blocks = _version_subblocks([(0x0422, 0x04B0)], "CompanyName")
    assert blocks[0] == "\\StringFileInfo\\042204B0\\CompanyName"


def test_every_listed_language_is_tried_in_order():
    blocks = _version_subblocks([(0x0407, 0x04E4), (0x0411, 0x04B0)], "ProductName")
    assert blocks[:2] == ["\\StringFileInfo\\040704E4\\ProductName",
                          "\\StringFileInfo\\041104B0\\ProductName"]


def test_the_english_and_neutral_blocks_remain_the_fallback():
    blocks = _version_subblocks([], "CompanyName")
    assert blocks == ["\\StringFileInfo\\040904B0\\CompanyName",
                      "\\StringFileInfo\\040904E4\\CompanyName",
                      "\\StringFileInfo\\000004B0\\CompanyName"]


def test_a_block_is_not_asked_twice():
    blocks = _version_subblocks([(0x0409, 0x04B0)], "CompanyName")
    assert len(blocks) == len(set(blocks))
