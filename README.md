# cichlid-file-management

Shared utilities for the McGrath lab cichlid pipeline: `FileManager` (project
directory layout + rclone-backed Dropbox I/O) and `LogParser`.

Both the Raspberry Pi data-collection repo and the `bower_building_ethology`
analysis package depend on this, so the directory contract lives in one place.

## Install

```bash
pip install git+https://github.com/McGrathLab/cichlid-file-management.git
```

For local development against a checkout:

```bash
pip install -e .
```

## Use

```python
from cichlid_file_management import FileManager, LogParser
```

## Requirements

- Python 3.8+
- pandas
- `rclone` on the PATH (invoked via subprocess for cloud I/O; installed separately)
