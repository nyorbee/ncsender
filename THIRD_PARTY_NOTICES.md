# Third-party software

ncsender's own code is under the MIT license (see `LICENSE`). It uses the Python libraries below. The source repository does
not contain them. The release `ncsender.exe` is built with Nuitka and **does contain them** (compiled in), so their licenses
apply to the exe.

License information read from the package metadata on PyPI (Python 3.13, win_amd64), checked 2026-10-27:

| Package | Used for | License |
|---|---|---|
| paramiko | SSH / SFTP | LGPL-2.1 |
| keyring | system keyring (Windows Credential Manager) | MIT |
| tkinterdnd2 | drag & drop | MIT |
| bcrypt, PyNaCl | crypto used by paramiko | Apache-2.0 |
| cryptography | crypto used by paramiko | Apache-2.0 OR BSD-3-Clause |
| cffi | dependency | MIT-0 |
| pycparser | dependency | BSD-3-Clause |
| invoke | dependency of paramiko | BSD-2-Clause |
| jaraco.classes, jaraco.context, jaraco.functools, more-itertools | dependencies of keyring | MIT |
| jeepney, SecretStorage | dependencies of keyring (Linux only) | MIT, BSD-3-Clause |

Not re-verified in this check: Python itself (PSF License) and the Nuitka runtime that is part of the compiled exe.

## About paramiko (LGPL-2.1)
The exe contains paramiko. The LGPL asks that you can replace the library with your own version. Because this project is
open source, you can do that: install another paramiko version and run or rebuild from source (see the README).
The license texts are available from each project's page on PyPI / GitHub.

This list was compiled carefully but may be incomplete. It is not legal advice.
