"""The ISO 10303 stack of freecad-nxstyle, vendored (SPEC FR-I3).

Source: https://github.com/Foadsf/freecad-nxstyle, commit 1b2fe899a7f42b75a3f39fed7ec0c8f761747f0c
(NXStyle/nxstyle/p21.py, express.py, express_eval.py, iso10303.py, resources/iso10303/
supplement.exp, and tools/iso10303_dictionary.py as dictionary_tool.py). Copyright the
freecad-nxstyle contributors. Licensed there under LGPL-2.1-or-later; included here under the
GNU Affero General Public License 3.0 or later, as LGPL-2.1 section 3 permits (conversion to the
GNU GPL, version 2 or later, whose version 3 is compatible with the AGPL-3.0).

The modules are kept as they are upstream, so fixes can flow both ways; ruff and mypy skip this
folder. The only changes: dictionary_tool.py imports express from this package and finds
supplement.exp next to itself. gcad.iso10303.schemas configures where schemas are cached.
"""
