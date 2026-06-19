"""cichlid_file_management — shared project-data utilities for the McGrath lab pipeline.

Used by both the Raspberry Pi data-collection repo and the
bower_building_ethology analysis package, so there is a single source of truth
for the project directory layout and rclone-backed I/O.
"""

from cichlid_file_management.base_file_manager import BaseFileManager
from cichlid_file_management.log_parser import LogParser

__all__ = ["FileManager", "LogParser"]
__version__ = "0.1.0"
