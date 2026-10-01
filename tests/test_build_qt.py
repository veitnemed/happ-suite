from types import SimpleNamespace
from unittest.mock import patch, Mock

import pytest

from scripts.build import _exclude_windows_icu


def pe(imports=(), exports=()):
    binary = SimpleNamespace(
        DIRECTORY_ENTRY_IMPORT=[SimpleNamespace(dll=b"icuuc.dll", imports=[SimpleNamespace(name=x) for x in imports])],
        DIRECTORY_ENTRY_EXPORT=SimpleNamespace(symbols=[SimpleNamespace(name=x) for x in exports]))
    context = Mock()
    context.__enter__ = Mock(return_value=binary)
    context.__exit__ = Mock(return_value=False)
    return context


def package(tmp_path):
    internal = tmp_path / "_internal"
    (internal / "PySide6").mkdir(parents=True)
    (internal / "PySide6" / "Qt6Core.dll").write_bytes(b"fixture")
    icu = internal / "icuuc.dll"
    icu.write_bytes(b"fixture")
    return icu


def test_windows_icu_not_shadowed_by_path_dll(tmp_path):
    icu = package(tmp_path)
    with patch.dict("sys.modules", {"pefile": SimpleNamespace(PE=Mock(side_effect=[pe(imports=[b"ucnv_open"]), pe(exports=[b"ucnv_open"])]))}):
        _exclude_windows_icu(tmp_path)
    assert not icu.exists()


def test_custom_versioned_icu_is_preserved(tmp_path):
    icu = package(tmp_path)
    with patch.dict("sys.modules", {"pefile": SimpleNamespace(PE=Mock(return_value=pe(imports=[b"ucnv_open_78"])))}):
        _exclude_windows_icu(tmp_path)
    assert icu.exists()


def test_missing_system_api_fails_without_removing_dll(tmp_path):
    icu = package(tmp_path)
    with patch.dict("sys.modules", {"pefile": SimpleNamespace(PE=Mock(side_effect=[pe(imports=[b"ucnv_open"]), pe(exports=[])]))}):
        with pytest.raises(RuntimeError, match="Windows ICU"):
            _exclude_windows_icu(tmp_path)
    assert icu.exists()
