# Dependencies and distribution boundaries

This repository references installed packages; it does not vendor their code.

| Dependency | License family / purpose |
|---|---|
| Flask, Werkzeug | BSD-3-Clause; local HTTP service |
| python-pptx | MIT; source-template inspection and package auditing |
| Pillow | HPND/PIL license; image inspection and diagnostic inventories |
| lxml | BSD-3-Clause; XML processing |
| pypdf | BSD-3-Clause; text and original image extraction |
| requests | Apache-2.0; reproducible HTTP acceptance scripts |
| pytest | MIT; development checks |

Use the exact license texts supplied with the installed distributions. Microsoft PowerPoint/Word and fonts require their own valid licenses.

PPT Master is an external, complete original distribution. Its license and attribution guard apply independently. Obtain a legitimate copy and set PPTMASTER_SKILL_DIR; this repository neither grants rights to it nor modifies its scripts. Optional historic OpenCode hosting is likewise external. Runtime paths in local-config.json are machine configuration and are excluded from Git.
