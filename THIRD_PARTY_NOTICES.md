# Qt runtime notices

Relay Studio source remains under the MIT license in `LICENSE`.
The desktop interface uses unmodified PySide6, Shiboken6 and Qt 6.11.2.
PySide6/Qt are separate libraries, used here under LGPL version 3.
The LGPL text and the GPL version 3 text incorporated by it are in `licenses/`.

Copyright (C) The Qt Company Ltd. and other contributors.

Source and licensing information:

- Qt for Python: https://code.qt.io/cgit/pyside/pyside-setup.git/
- Qt modules: https://code.qt.io/cgit/qt/qtbase.git/
- Version tags: `v6.11.2` (use the tag matching the distributed libraries).
- Qt for Python license notices: https://doc.qt.io/qtforpython-6/licenses.html
- Qt third party notices: https://doc.qt.io/qt-6/licenses-used-in-qt.html

The onedir application dynamically loads Qt DLLs and the Windows platform
plugin from `_internal/PySide6`. These libraries can be replaced with compatible
modified builds. Relay Studio does not prohibit reverse engineering for
debugging modifications to these libraries. Rebuild the application with
`python scripts/build.py` after installing a compatible modified PySide6 build.
No Qt library source modifications are made by this project.

Other dependencies (Python, Pillow, requests, PyYAML, psutil, pystray, pywin32)
retain their respective licenses. Their wheel license files are collected
into the build's `licenses/dependencies` directory when available.
